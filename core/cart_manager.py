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

        # SIMULAZIONE INVIO API REST
        json_payload = json.dumps(payload, indent=4, ensure_ascii=False)
        print("\n" + "="*50)
        print("📦 [MOCK ERP API] INVIO ORDINE AL GESTIONALE...")
        print(f"🔗 ENDPOINT CHIAMATO: https://api.saas-food.com/v1/orders/webhook")
        print(f"📄 PAYLOAD JSON INOLTRATO:\n{json_payload}")
        print("="*50 + "\n")
        
        self.clear_cart(session_id)
        return True, json_payload

app_cart = CartManager()
