from datetime import datetime
import json

class CartManager:
    _instance = None
    _carts = {}  # Mappa: session_id -> { "tenant_id": str, "items": list, "dati_cliente": dict, "created_at": datetime, "last_active": datetime }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CartManager, cls).__new__(cls)
        return cls._instance

    def _cleanup_old_carts(self):
        now = datetime.now()
        scadute = [sid for sid, data in self._carts.items() if (now - data.get("last_active", now)).total_seconds() > 86400]
        for sid in scadute:
            del self._carts[sid]

    def init_cart(self, session_id: str, tenant_id: str):
        self._cleanup_old_carts()
        if session_id not in self._carts:
            self._carts[session_id] = {
                "tenant_id": tenant_id,
                "items": [],
                "dati_cliente": {},
                "created_at": datetime.now(),
                "last_active": datetime.now()
            }
        else:
            self._carts[session_id]["last_active"] = datetime.now()
        return self._carts[session_id]

    def get_cart(self, session_id: str):
        if session_id in self._carts:
            self._carts[session_id]["last_active"] = datetime.now()
        return self._carts.get(session_id)

    def update_cart(self, session_id: str, items: list, dati_cliente: dict = None):
        """Aggiorna in blocco il carrello in fase di estrazione finale."""
        self._cleanup_old_carts()
        if session_id in self._carts:
            self._carts[session_id]["items"] = items
            if dati_cliente:
                self._carts[session_id]["dati_cliente"].update(dati_cliente)
            self._carts[session_id]["last_active"] = datetime.now()
            return True
        return False

    def clear_cart(self, session_id: str):
        if session_id in self._carts:
            del self._carts[session_id]

    def inoltra_ordine_erp(self, session_id: str):
        """
        Simula l'invio del carrello al gestionale del tenant tramite API REST (Webhook).
        In produzione, qui ci sarebbe un request.post() verso l'ERP del cliente (es. Zucchetti).
        """
        cart = self.get_cart(session_id)
        if not cart or not cart["items"]:
            return False, "Carrello vuoto o inesistente."

        payload = {
            "tenant_id": cart["tenant_id"],
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "cliente": cart["dati_cliente"],
            "righe_ordine": cart["items"]
        }

        import os
        webhook_url = os.getenv("ERP_WEBHOOK_URL", "").strip()
        if not webhook_url:
            # Nessun gestionale configurato: l'ordine NON viene mandato a indirizzi sconosciuti, si salva in locale
            # (data/ordini/*.json) per il commerciale. Impostare ERP_WEBHOOK_URL per l'invio automatico.
            try:
                cartella = os.getenv("ORDINI_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ordini")
                os.makedirs(cartella, exist_ok=True)
                nome = f"ordine_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(session_id)[:8]}.json"
                with open(os.path.join(cartella, nome), "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                self.clear_cart(session_id)
                return True, "Ordine registrato: il commerciale ti inviera' la conferma con la quotazione."
            except OSError as e:
                print(f"[ERRORE ORDINE] salvataggio locale fallito: {e}")
                return False, "Non sono riuscito a registrare l'ordine. Riprova tra qualche istante."

        import requests
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            self.clear_cart(session_id)
            return True, "Ordine acquisito dal gestionale."
        except requests.exceptions.RequestException as e:
            print(f"[ERRORE ERP] Invio ordine fallito: {e}")
            return False, "Il gestionale non ha risposto. Riprova piu' tardi."

app_cart = CartManager()
