"""TEİAŞ Yük Tevzi Bilgi Sistemi (YTBS) kamuya açık istatistikleri.

YTBS verileri TEİAŞ'ın ifadesiyle geçici işletme verisidir: geriye dönük düzeltmeye açıktır,
lisanssız üretim ay sonuna kadar tahminidir. Kaynak yalnız Türkiye IP'lerinden yanıt verir;
bu nedenle veri Türkiye'de çalışan `ytbs-sync` ile çekilip CSV deposuna yazılır.
"""

YTBS_BASE_URL = "https://ytbsbilgi.teias.gov.tr"
YTBS_PAGE_PATH = "/ytbsbilgi/frm_istatistikler.jsf"
YTBS_PAGE_URL = YTBS_BASE_URL + YTBS_PAGE_PATH
YTBS_DATA_NOTE = (
    "TEİAŞ YTBS geçici işletme verisi; geriye dönük düzeltmeye açıktır, lisanssız üretim "
    "ay sonuna kadar tahminidir. Resmî aylık/yıllık TEİAŞ istatistiklerinin yerine geçmez."
)
