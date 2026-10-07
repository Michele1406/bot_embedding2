# -*- coding: utf-8 -*-
"""Il prompt di sistema contiene le regole che il resto del codice presuppone (contesto <-> prompt devono restare allineati)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.system_prompt_v2 import build_modular_prompt


class TestPrompt(unittest.TestCase):
    def test_regole_chiave(self):
        p = build_modular_prompt("ricerca_specifica", "horeca", "")
        for frase in ("ESCLUSIVAMENTE", "MODALITA' D'USO", "OLIVE", "Olive: ...", "ABBINAMENTI VERIFICATI", "SCHEDA AZIENDA",
                      "SICUREZZA", "DIETA VEGANA", "dedotto dagli ingredienti", "MEMORIA", "OUT OF STOCK", "Niente prezzi"):
            self.assertIn(frase, p, frase)

    def test_canale_e_dieta(self):
        self.assertIn("RETAIL", build_modular_prompt("x", "retail", ""))
        self.assertIn("HORECA", build_modular_prompt("x", "horeca", ""))
        self.assertIn("VEGANO", build_modular_prompt("x", "horeca", "vegano"))
        self.assertNotIn("REGOLE DIETA", build_modular_prompt("x", "horeca", ""))

    def test_composizione_due_modalita(self):
        # D5: Nino sa comporre in modo completo e guidato; le regole valgono anche nei follow-up
        for tipo in ("composizione_piatto", "ricerca_specifica"):
            p = build_modular_prompt(tipo, "horeca", "")
            for frase in ("COMPOSIZIONE", "COMPLETA", "GUIDATA", "IN ALTERNATIVA", "mai due salami", "Opzione B"):
                self.assertIn(frase, p, frase)

    def test_marcatori_del_contesto_spiegati(self):
        """Ogni etichetta che il codice mette nel contesto e che il modello deve capire e' citata nel prompt."""
        p = build_modular_prompt("x", "horeca", "")
        for marcatore in ("ABBINAMENTI VERIFICATI", "SCHEDA AZIENDA", "Olive:", "Modalita d'uso", "Allergeni:",
                          "ALLERGIE", "ALTERNATIVA VERIFICATA", "INFO SO FOOD", "CONFERMO", "MODALITA'",
                          "COLLEGAMENTI VERIFICATI DEL PRODOTTO", "Guida tecnica per", "GUIDA ALLA SCELTA", "Regione del produttore", "Origine", "serve anche",
                          "Racconto del prodotto", "Dati tecnici", "COSTRUZIONE TAGLIERE INSIEME", "PANORAMICA DAL CATALOGO",
                          "ALTRI PRODOTTI PERTINENTI",
                          "Piatti del ricettario So Food", "Alternative verificate"):
            self.assertIn(marcatore.split(":")[0].split(" (")[0], p, marcatore)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPersonalita(unittest.TestCase):
    def test_chi_siamo_da_sys(self):
        # sofood/SOFOOD.txt (scritto dall'azienda) entra nel prompt: personalita' di Nino
        from core.system_prompt_v2 import build_modular_prompt, profilo_sofood
        if profilo_sofood():
            p = build_modular_prompt("x", "horeca", "")
            self.assertIn("CHI SIAMO", p)
            self.assertIn("qualit", p)
