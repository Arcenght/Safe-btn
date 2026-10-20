/*
 * SafeBtn - firmware de teste
 *
 * - Botao (switch de teclado) em GPIO com debounce
 * - Dois eventos:  EVT_SHORT (toque)  e  EVT_LONG (segurar LONG_PRESS_MS)
 * - Envia o evento por BLE com INDICATION (o celular confirma o recebimento)
 * - O evento so sai da fila DEPOIS da confirmacao. Se o celular nao estiver
 *   conectado/inscrito, ou a confirmacao nao vier, o evento fica na fila (RAM)
 *   e e reenviado quando reconectar (proximo passo: fila em flash)
 * - Pode haver duplicados (queda de link entre envio e confirmacao): o
 *   receptor deve descartar por (tipo, seq, uptime).
 * - Sem LED, vibracao ou som no botao. Log so pela USB (debug).
 *
 * Payload da caracteristica (6 bytes, little-endian):
 *   [0]    tipo   (1 = toque, 2 = longo)
 *   [1]    seq    (contador, 0-255, serve para o app descartar duplicados)
 *   [2..5] uptime em segundos quando o botao foi apertado (uint32)
 */

#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/settings/settings.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(safebtn, LOG_LEVEL_INF);

/* ---------- Configuracao ---------- */
#define DEBOUNCE_MS        30
#define LONG_PRESS_MS      5000   /* 5 s (voce pediu 5~10 s) */
#define QUEUE_LEN          8
/* 1: evento SHORT sai assim que aperta (mais rapido, e o LONG vem depois
 *    como escalada se continuar segurando).
 * 0: evento SHORT so sai ao soltar, e apenas se nao virou LONG. */
#define SHORT_ON_PRESS     1
/* 1: exige pareamento/criptografia para assinar as notificacoes */
#define REQUIRE_ENCRYPTION 0

/* ---------- Tipos ---------- */
enum evt_type { EVT_SHORT = 1, EVT_LONG = 2 };

struct __packed btn_evt {
	uint8_t  type;
	uint8_t  seq;
	uint32_t uptime_s;
};

/* ---------- UUIDs (gerados para este projeto) ---------- */
#define SVC_UUID_VAL BT_UUID_128_ENCODE(0x5a1b0001, 0x7c3d, 0x4e8f, 0x9a2b, 0x1c4d5e6f7a80)
#define EVT_UUID_VAL BT_UUID_128_ENCODE(0x5a1b0002, 0x7c3d, 0x4e8f, 0x9a2b, 0x1c4d5e6f7a80)

static struct bt_uuid_128 svc_uuid = BT_UUID_INIT_128(SVC_UUID_VAL);
static struct bt_uuid_128 evt_uuid = BT_UUID_INIT_128(EVT_UUID_VAL);

#if REQUIRE_ENCRYPTION
#define CCC_PERM (BT_GATT_PERM_READ_ENCRYPT | BT_GATT_PERM_WRITE_ENCRYPT)
#else
#define CCC_PERM (BT_GATT_PERM_READ | BT_GATT_PERM_WRITE)
#endif

/* ---------- Estado ---------- */
static const struct gpio_dt_spec btn = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static struct gpio_callback btn_cb;

static bool pressed;
static bool long_fired;
static int64_t press_t;
static uint8_t seq;
static volatile bool link_up;
static volatile bool indicate_enabled;
static atomic_t indicating;        /* 1 = aguardando confirmacao */
static volatile uint8_t ack_err;
static struct btn_evt inflight;     /* evento em voo (precisa ficar valido) */
static struct bt_gatt_indicate_params ind_params;

static struct k_msgq evt_q;
static char evt_q_buf[QUEUE_LEN * sizeof(struct btn_evt)];

static void debounce_handler(struct k_work *work);
static void long_handler(struct k_work *work);
static void flush_handler(struct k_work *work);
static void hb_handler(struct k_work *work);

static K_WORK_DELAYABLE_DEFINE(debounce_work, debounce_handler);
static K_WORK_DELAYABLE_DEFINE(long_work, long_handler);
static K_WORK_DELAYABLE_DEFINE(flush_work, flush_handler);
static K_WORK_DELAYABLE_DEFINE(hb_work, hb_handler);

/* ---------- GATT ---------- */
static void ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	indicate_enabled = (value == BT_GATT_CCC_INDICATE);
	LOG_INF("Indications %s", indicate_enabled ? "ATIVADAS" : "desativadas");
	if (indicate_enabled) {
		k_work_reschedule(&flush_work, K_NO_WAIT);
	}
}

BT_GATT_SERVICE_DEFINE(safe_svc,
	BT_GATT_PRIMARY_SERVICE(&svc_uuid),
	BT_GATT_CHARACTERISTIC(&evt_uuid.uuid, BT_GATT_CHRC_INDICATE,
			       BT_GATT_PERM_NONE, NULL, NULL, NULL),
	BT_GATT_CCC(ccc_cfg_changed, CCC_PERM),
);

/* ---------- Fila e envio (com confirmacao) ---------- */
static void ack_handler(struct k_work *work);
K_WORK_DEFINE(ack_work, ack_handler);

/* Chamado pelo BLE quando o celular confirma (ou falha) a indication */
static void indicate_cb(struct bt_conn *conn,
			struct bt_gatt_indicate_params *params, uint8_t err)
{
	ack_err = err;
	k_work_submit(&ack_work); /* mexe na fila so a partir da system workqueue */
}

static void ack_handler(struct k_work *work)
{
	if (ack_err == 0) {
		struct btn_evt head;

		/* tira da fila so se o primeiro ainda e o que foi confirmado */
		if (k_msgq_peek(&evt_q, &head) == 0 &&
		    head.seq == inflight.seq && head.type == inflight.type) {
			k_msgq_get(&evt_q, &head, K_NO_WAIT);
		}
		LOG_INF("CONFIRMADO tipo=%u seq=%u", inflight.type, inflight.seq);
	} else {
		LOG_WRN("indication falhou (ATT 0x%02x), vai reenviar", ack_err);
	}

	atomic_set(&indicating, 0);
	k_work_reschedule(&flush_work, ack_err ? K_MSEC(500) : K_NO_WAIT);
}

static void flush_handler(struct k_work *work)
{
	int err;

	if (!indicate_enabled) {
		if (k_msgq_num_used_get(&evt_q)) {
			LOG_WRN("%u evento(s) aguardando o celular",
				k_msgq_num_used_get(&evt_q));
		}
		return;
	}
	if (atomic_get(&indicating)) {
		return; /* uma por vez: espera a confirmacao da anterior */
	}
	if (k_msgq_peek(&evt_q, &inflight) != 0) {
		return; /* fila vazia */
	}

	memset(&ind_params, 0, sizeof(ind_params));
	ind_params.attr = &safe_svc.attrs[2]; /* valor da caracteristica */
	ind_params.func = indicate_cb;
	ind_params.data = &inflight;
	ind_params.len = sizeof(inflight);

	atomic_set(&indicating, 1);
	err = bt_gatt_indicate(NULL, &ind_params);
	if (err) {
		atomic_set(&indicating, 0);
		LOG_ERR("indicate falhou (%d), evento mantido na fila", err);
		k_work_reschedule(&flush_work,
				  (err == -ENOMEM || err == -EBUSY) ? K_MSEC(50)
								    : K_MSEC(1000));
		return;
	}
	LOG_INF("ENVIADO tipo=%u seq=%u, aguardando confirmacao",
		inflight.type, inflight.seq);
}

static void push_event(enum evt_type type)
{
	struct btn_evt e = {
		.type = type,
		.seq = seq++,
		.uptime_s = (uint32_t)(k_uptime_get() / 1000),
	};

	if (k_msgq_put(&evt_q, &e, K_NO_WAIT) != 0) {
		struct btn_evt old;

		/* fila cheia: descarta o mais antigo, o mais novo importa mais */
		k_msgq_get(&evt_q, &old, K_NO_WAIT);
		k_msgq_put(&evt_q, &e, K_NO_WAIT);
		LOG_WRN("fila cheia, evento antigo descartado");
	}

	LOG_INF("EVENTO %s (seq=%u)", type == EVT_SHORT ? "TOQUE" : "LONGO", e.seq);
	k_work_reschedule(&flush_work, K_NO_WAIT);
}

/* ---------- Log periodico (so para depuracao pela USB) ---------- */
static void hb_handler(struct k_work *work)
{
	LOG_INF("vivo | link=%d indic=%d fila=%u emvoo=%d",
		link_up, indicate_enabled, k_msgq_num_used_get(&evt_q),
		(int)atomic_get(&indicating));
	k_work_reschedule(&hb_work, K_SECONDS(5));
}

/* ---------- Botao ---------- */
static void debounce_handler(struct k_work *work)
{
	bool now = gpio_pin_get_dt(&btn) > 0;

	if (now == pressed) {
		return; /* ruido, nada mudou */
	}
	pressed = now;

	if (pressed) {
		press_t = k_uptime_get();
		long_fired = false;
		k_work_reschedule(&long_work, K_MSEC(LONG_PRESS_MS));
#if SHORT_ON_PRESS
		push_event(EVT_SHORT);
#endif
	} else {
		k_work_cancel_delayable(&long_work);
		LOG_INF("solto apos %lld ms", k_uptime_get() - press_t);
#if !SHORT_ON_PRESS
		if (!long_fired) {
			push_event(EVT_SHORT);
		}
#endif
	}
}

static void long_handler(struct k_work *work)
{
	if (pressed) {
		long_fired = true;
		push_event(EVT_LONG);
	}
}

static void btn_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
	k_work_reschedule(&debounce_work, K_MSEC(DEBOUNCE_MS));
}

/* ---------- Advertising / conexao ---------- */
static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME,
		sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, SVC_UUID_VAL),
};

static void start_adv(void)
{
	/* 1,0 a 1,2 s: economico, mas ainda reconecta rapido */
	int err = bt_le_adv_start(
		BT_LE_ADV_PARAM(BT_LE_ADV_OPT_CONN, 0x0640, 0x0780, NULL),
		ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));

	if (err) {
		LOG_ERR("advertising falhou (%d)", err);
	} else {
		LOG_INF("anunciando como '%s'", CONFIG_BT_DEVICE_NAME);
	}
}

static void on_connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		LOG_ERR("conexao falhou (0x%02x)", err);
		return;
	}
	link_up = true;
	LOG_INF("celular conectado");
}

static void on_disconnected(struct bt_conn *conn, uint8_t reason)
{
	link_up = false;
	indicate_enabled = false;
	atomic_set(&indicating, 0); /* confirmacao pendente nao vai chegar */
	LOG_INF("desconectado (0x%02x)", reason);
}

static void on_recycled(void)
{
	start_adv(); /* so pode reiniciar aqui, depois que a conexao foi liberada */
}

BT_CONN_CB_DEFINE(conn_cbs) = {
	.connected    = on_connected,
	.disconnected = on_disconnected,
	.recycled     = on_recycled,
};

/* ---------- main ---------- */
int main(void)
{
	int err;

	k_msgq_init(&evt_q, evt_q_buf, sizeof(struct btn_evt), QUEUE_LEN);

	if (!gpio_is_ready_dt(&btn)) {
		LOG_ERR("GPIO do botao nao esta pronto");
		return 0;
	}
	gpio_pin_configure_dt(&btn, GPIO_INPUT);
	gpio_pin_interrupt_configure_dt(&btn, GPIO_INT_EDGE_BOTH);
	gpio_init_callback(&btn_cb, btn_isr, BIT(btn.pin));
	gpio_add_callback(btn.port, &btn_cb);

	err = bt_enable(NULL);
	if (err) {
		LOG_ERR("bt_enable falhou (%d)", err);
		return 0;
	}
	if (IS_ENABLED(CONFIG_BT_SETTINGS)) {
		settings_load();
	}

	start_adv();

#if DT_HAS_CHOSEN(zephyr_console) && defined(CONFIG_UART_LINE_CTRL)
	/* Espera ate 5 s o PC abrir a porta serial, senao o banner se perde */
	{
		const struct device *con = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
		uint32_t dtr = 0;

		if (device_is_ready(con)) {
			for (int i = 0; i < 50 && !dtr; i++) {
				if (uart_line_ctrl_get(con, UART_LINE_CTRL_DTR, &dtr)) {
					break;
				}
				k_msleep(100);
			}
		}
	}
#endif

	LOG_INF("SafeBtn pronto");
	k_work_reschedule(&hb_work, K_SECONDS(5));
	return 0;
}