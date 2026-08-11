# Resmî veri kaynağı keşfi

Keşif tarihi: 2026-08-11

Bu envanter yalnızca TEİAŞ ve EÜAŞ'ın resmî alan adlarında yayınlanan kaynakları kapsar.
EPİAŞ kaynakları bilinçli olarak kapsam dışıdır. `HIGH`, makinece okunabilir ve bu projede
doğrulanmış kaynağı; `MEDIUM`, resmî fakat kırılgan veya yalnız HTML/PDF kaynağı;
`NOT AVAILABLE`, üretim aracı olarak güvenle kullanılamayan kaynağı ifade eder.

| Institution | Dataset | Official Source URL | Format | Update Frequency | Historical Depth | Access Method | MCP Feasibility | Notes |
|---|---|---|---|---|---|---|---|---|
| TEİAŞ | Aylık üretim-tüketim | https://www.teias.gov.tr/aylik-elektrik-uretim-tuketim-raporlari | JSON katalog + XLSX | Aylık | 2019-günümüz | `/api/gallery` kataloğu, `/file/{slug}` indirme | HIGH | Her yıl galeride güncel/kümülatif çalışma kitabı yayınlanıyor. |
| TEİAŞ | Yıllık üretim, tüketim, kayıplar | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS/XLSX | Yıllık | 1923-2024 (tabloya göre değişir) | `/api/gallery?locale=tr-TR&slug=iii-...-{year}` | HIGH | Kaynak, kuruluş ve aylık dağılım tabloları ayrı dosyalardır. |
| TEİAŞ | Kurulu güç | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS/XLSX | Yıllık | 1913-2024; kaynak kırılımı 1940-2024 | `/api/gallery?locale=tr-TR&slug=i-kurulu-guc-{year}` | HIGH | Kaynak ve üretici kuruluş kırılımları bulunur. |
| TEİAŞ | Güncel/aylık kurulu güç | https://ytbsbilgi.teias.gov.tr/ytbsbilgi/frm_istatistikler.jsf | JSF + Excel/PDF | Günlük/aylık | 2017-günümüz | JSF form gönderimi | MEDIUM | Resmî ve herkese açık; kararlı bir JSON/XHR sözleşmesi doğrulanamadığı için production tool'a alınmadı. |
| TEİAŞ | Puant, maksimum/minimum günlük tüketim | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS/XLSX | Yıllık | 1980-2024 | `ii-turkiye-kurulu-gucunun-kullanim-degerleri-{year}` galerisi | HIGH | Ani/saatlik puant ile maksimum/minimum günlük tüketim tabloları mevcut. |
| TEİAŞ | Saatlik tüketim ve günlük yük eğrisi | https://ytbsbilgi.teias.gov.tr/ytbsbilgi/frm_istatistikler.jsf | JSF/HTML, Excel/PDF (UI) | Günlük | 2017-günümüz (UI) | Halka açık JSF arayüzü | NOT AVAILABLE | 2026-08-11 yeniden doğrulama: bu ortamdan TLS handshake reset (`Connection reset by peer`); kararlı JSON/XHR sözleşmesi hâlâ yok. EPİAŞ saatlik tüketim bilinçli olarak kullanılmıyor. |
| TEİAŞ | Günlük şebeke frekansı | https://www.teias.gov.tr (galeri: `gunluk-frekans-bilgisi`) | JSON katalog + CSV/TXT | Günlük (saniyelik) | En az 2024-12 → 2026-08 | `/api/gallery` + `/file/{slug}` | HIGH | Resmî frekans serisi doğrulandı (Hz); yük eğrisi (MW) değildir. |
| TEİAŞ | Günlük işletme raporları | https://www.teias.gov.tr (galeri: `gunluk-isletme-raporlari`) | PDF | Günlük (eski) | Yalnız 2019-12 arşivi | `/api/gallery` | NOT AVAILABLE | Güncel seri yok; yalnız eski PDF arşivi. |
| TEİAŞ | İthalat/ihracat toplamları | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS | Yıllık | İthalat 1975-2024, ihracat 1990-2024 | `v-ithalat-ihracat-{year}` galerisi | HIGH | Aylık toplam tabloları vardır. |
| TEİAŞ | Ülke bazında ithalat/ihracat | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | DOCX grafik | Yıllık | En az 2024 | dosya indirme | NOT AVAILABLE | Ham ülke tablosu doğrulanmadı; grafikten veri tahmini yapılmıyor. |
| TEİAŞ | NTC/enterkonneksiyon kapasitesi | https://www.teias.gov.tr/enterkonneksiyonlar | HTML/PDF | Düzensiz/yıllık | Ülkeye göre değişir | sayfa/dosya | MEDIUM | Ülke sayfaları heterojen; bu sürümde production tool'a alınmadı. |
| TEİAŞ | İletim hatları ve trafolar | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS | Yıllık | Hatlar 1979-2024, trafolar 1980-2024 | `vi-enerji-nakil-hat-ve-trafolari-{year}` galerisi | HIGH | Gerilim kırılımı, uzunluk, trafo adet ve güç tabloları bulunur. |
| TEİAŞ | Güncel sistem özeti | https://www.teias.gov.tr/sayilarla-elektrik-iletimi | HTML | Aylık | Güncel | HTML | MEDIUM | Kurulu güç, hat, trafo, üretim, tüketim, ithalat ve ihracat özetidir; HTML son seçenek olarak kullanılabilir. |
| TEİAŞ | Yenilenebilir üretim ve kapasite payı | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLSX | Yıllık | 2000-2024 | kurulu güç ve üretim galerileri | HIGH | Yenilenebilir pay tabloları doğrudan yayınlanır. |
| TEİAŞ | EÜAŞ termik/hidrolik santral portföyü | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS | Yıllık | Güncel raporda 2024 | `i-kurulu-guc-2024` galerisi | HIGH | Metadata `source=TEİAŞ`, `subject=EÜAŞ` olarak döner. |
| TEİAŞ | EÜAŞ kurulu gücü ve üretimi | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS/XLSX | Yıllık | Kurulu güç 2006-2024; üretim 2014-2024 | üretici kuruluş tabloları | HIGH | Metadata `source=TEİAŞ`, `subject=EÜAŞ` olarak döner. |
| EÜAŞ | Santral portföyü ve santral detayları | https://www.euas.gov.tr/santraller | HTML | Düzensiz | Güncel | HTML sayfaları | NOT AVAILABLE | 2026-08-11: `www.euas.gov.tr` TLS reset / HTTP 500; production veri yolu değil. |
| EÜAŞ | Kurulu güç/aylık üretim ana sayfa özeti | https://www.euas.gov.tr/ | HTML | Düzensiz | Güncel | HTML | NOT AVAILABLE | Sayfa erişimi kararsız; 0 değerleri ve 500 yanıtları görüldü. |
| EÜAŞ | Yıllık/aylık üretim | https://www.euas.gov.tr/ | HTML/rapor | Belirsiz | Belirsiz | site araması | NOT AVAILABLE | Makinece okunabilir resmî seri veya doğrulanmış faaliyet raporu arşivi bulunamadı. |
| EÜAŞ | Operasyonel performans, duruş, bakım | https://www.euas.gov.tr/ | Belirlenemedi | Belirlenemedi | Belirlenemedi | — | NOT AVAILABLE | 2026-08-11: EÜAŞ sitesi erişilemez; açık ve doğrulanmış operasyon/duruş zaman serisi yok. |
| EÜAŞ | Yatırım/rehabilitasyon zaman serisi | https://www.euas.gov.tr/ | HTML/duyuru/PDF | Düzensiz | Belirsiz | site araması | NOT AVAILABLE | Tekil haber/PDF'ler var; EÜAŞ sitesinden bütünlüklü, karşılaştırılabilir açık veri seti doğrulanamadı. |

## Doğrulanmış erişim sözleşmeleri

- Sayfa içeriği: `GET https://www.teias.gov.tr/api/page?locale=tr-TR&slug={page_slug}`
- Galeri kataloğu (tümü): `GET https://www.teias.gov.tr/api/gallery?locale=tr-TR`
- Galeri detayı: `GET https://www.teias.gov.tr/api/gallery?locale=tr-TR&slug={gallery_slug}`
- Dosya indirme: `GET https://webim.teias.gov.tr/file/{media.slug}?download`

### Güncellik seçim kuralı

Aynı veri seti için birden fazla yıllık galeri veya dosya varsa sunucu:

1. katalogdan en yüksek dönem yılını ve en yeni `publish_at`/`updated_at`/`created_at`
   damgasını seçer,
2. dosya adını veya tablo numarasını hard-code etmez; semantik başlık + kapsanan dönem
   aralığına göre en güncel dosyayı alır,
3. geçmiş tarih sorgularında en güncel kümülatif resmi seriyi kullanıp istenen döneme
   filtreler,
4. istenen güncel dönem yoksa eski veriyi sessizce döndürmez; `latest_available_period`
   ve `data_freshness` alanlarını metadata veya hata detayında açıklar.

Bu endpointler TEİAŞ sayfasının kendi JavaScript istemcisinde kullanılıyor ve keşif sırasında
JSON katalogları ile gerçek XLS/XLSX dosyaları indirilerek doğrulandı. Endpointler resmî fakat
belgelenmemiş web uygulaması sözleşmeleridir; şema değişikliği teknik risk olarak izlenmelidir.
