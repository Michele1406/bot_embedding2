import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.domain_rules import check_board_violations, prodotto_appartiene_a_famiglia

def run_tests():
    print("=== TEST SCRIPT: DOMAIN RULES (CONSTRAINT SOLVER) ===")
    
    # 1. Caso base corretto
    board_ok = [
        {"metadata": {"sottocategoria": "PROSCIUTTO CRUDO"}, "document": "Prosciutto di Parma DOP"},
        {"metadata": {"sottocategoria": "SALAME"}, "document": "Salame Felino IGP"},
        {"metadata": {"sottocategoria": "GORGONZOLA"}, "document": "Gorgonzola Dolce"},
        {"metadata": {"sottocategoria": "PECORINO"}, "document": "Pecorino Romano"}
    ]
    v = check_board_violations(board_ok)
    assert not v, f"Caso OK fallito: {v}"
    
    # 2. Due salumi macinati con sottocategoria esatta
    board_due_macinati_chiari = [
        {"metadata": {"sottocategoria": "SALAME"}, "document": "Salame Napoli"},
        {"metadata": {"sottocategoria": "FINOCCHIONA"}, "document": "Finocchiona IGP"}
    ]
    v = check_board_violations(board_due_macinati_chiari)
    assert len(v) == 1 and "Troppi insaccati macinati" in v[0]

    # 3. Bug storico: Finocchiona e Salame nascosti nel bucket "SALUMI INTERI/TRANCI"
    board_macinati_nascosti = [
        {"metadata": {"sottocategoria": "SALUMI INTERI/TRANCI"}, "document": "Salame Nostrano"},
        {"metadata": {"sottocategoria": "SALUMI INTERI/TRANCI"}, "document": "Finocchiona S/V"}
    ]
    v = check_board_violations(board_macinati_nascosti)
    assert len(v) == 1 and "Troppi insaccati macinati" in v[0], "Bug Finocchiona+Salame non risolto!"
    
    # 4. Troppi cotti
    board_due_cotti = [
        {"metadata": {"sottocategoria": "MORTADELLA"}, "document": "Mortadella Bologna"},
        {"metadata": {"sottocategoria": "PROSCIUTTO COTTO"}, "document": "Prosciutto Cotto Alta Qualità"}
    ]
    v = check_board_violations(board_due_cotti)
    assert len(v) == 1 and "Troppi salumi cotti" in v[0]
    
    # 5. Crosta fiorita ripetuta
    board_crosta_fiorita = [
        {"metadata": {"sottocategoria": "BRIE"}, "document": "Brie Francese"},
        {"metadata": {"sottocategoria": "TALEGGIO"}, "document": "Taleggio DOP"}
    ]
    v = check_board_violations(board_crosta_fiorita)
    assert len(v) == 1 and "crosta fiorita" in v[0]
    
    # 6. Due erborinati
    board_blu = [
        {"metadata": {"sottocategoria": "GORGONZOLA"}, "document": "Gorgonzola Piccante"},
        {"metadata": {"sottocategoria": "ROQUEFORT"}, "document": "Roquefort"}
    ]
    v = check_board_violations(board_blu)
    assert len(v) == 1 and "erborinato" in v[0]
    
    # 7. Formaggi freschi bagnati
    board_freschi = [
        {"metadata": {"sottocategoria": "BURRATA"}, "document": "Burrata Pugliese"},
        {"metadata": {"sottocategoria": "MOZZARELLA"}, "document": "Mozzarella di Bufala"}
    ]
    v = check_board_violations(board_freschi)
    assert len(v) == 1 and "freschi/umidi" in v[0]

    # 8. Terra e Mare mix
    board_terra_mare = [
        {"metadata": {"reparto": "SALUMI"}, "document": "Salame"},
        {"metadata": {"reparto": "MARE"}, "document": "Salmone Affumicato"}
    ]
    v = check_board_violations(board_terra_mare)
    assert len(v) == 1 and "mare e salumi di terra" in v[0]

    # 9. Terra e Mare consentito
    v2 = check_board_violations(board_terra_mare, allow_terra_mare=True)
    assert not v2

    # 10. Due prosciutti crudi ammessi
    board_due_crudi = [
        {"metadata": {"sottocategoria": "PROSCIUTTO CRUDO"}, "document": "Parma 24 Mesi"},
        {"metadata": {"sottocategoria": "PROSCIUTTO CRUDO"}, "document": "Jamon Iberico"}
    ]
    assert not check_board_violations(board_due_crudi)

    # 11. Tre prosciutti crudi vietati
    board_tre_crudi = board_due_crudi + [{"metadata": {"sottocategoria": "PROSCIUTTO CRUDO"}, "document": "San Daniele"}]
    v = check_board_violations(board_tre_crudi)
    assert len(v) == 1 and "Più di due prosciutti crudi" in v[0]
    
    # 12. Test prodotto_appartiene_a_famiglia singolo
    prod = {"metadata": {"sottocategoria": "SALUMI INTERI/TRANCI"}, "document": "Salame piccante calabrese"}
    assert prodotto_appartiene_a_famiglia(prod, "SALUMI_MACINATI")
    assert not prodotto_appartiene_a_famiglia(prod, "SALUMI_COTTI")
    
    # 13. Test prodotto che NON matcha niente
    prod2 = {"metadata": {"sottocategoria": "FORMAGGI STAGIONATI"}, "document": "Pecorino Sardo"}
    for famiglia in ["SALUMI_MACINATI", "SALUMI_COTTI", "FORMAGGI_ERBORINATI", "FORMAGGI_CROSTA_FIORITA", "FORMAGGI_FRESCHI_SPALMABILI"]:
        assert not prodotto_appartiene_a_famiglia(prod2, famiglia)

    # 14. Nomi incriminati inclusi nel messaggio di errore (importante per l'esclusione mirata)
    v = check_board_violations(board_due_cotti)
    assert "MORTADELLA BOLOGNA" in v[0] and "PROSCIUTTO COTTO" in v[0]

    print("[SUCCESS] Tutti i 14 test superati con successo! Il Constraint Solver funziona come previsto.")

if __name__ == "__main__":
    run_tests()
