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
| TEİAŞ | Saatlik tüketim ve günlük yük eğrisi | https://ytbsbilgi.teias.gov.tr/ytbsbilgi/frm_istatistikler.jsf | JSF/HTML, Excel/PDF | Günlük | 2017-günümüz | JSF form gönderimi | NOT AVAILABLE | Doğrulanmış, kararlı ve yeniden kullanılabilir makine endpointi tespit edilmedi. |
| TEİAŞ | İthalat/ihracat toplamları | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS | Yıllık | İthalat 1975-2024, ihracat 1990-2024 | `v-ithalat-ihracat-{year}` galerisi | HIGH | Aylık toplam tabloları vardır. |
| TEİAŞ | Ülke bazında ithalat/ihracat | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | DOCX grafik | Yıllık | En az 2024 | dosya indirme | NOT AVAILABLE | Ham ülke tablosu doğrulanmadı; grafikten veri tahmini yapılmıyor. |
| TEİAŞ | NTC/enterkonneksiyon kapasitesi | https://www.teias.gov.tr/enterkonneksiyonlar | HTML/PDF | Düzensiz/yıllık | Ülkeye göre değişir | sayfa/dosya | MEDIUM | Ülke sayfaları heterojen; bu sürümde production tool'a alınmadı. |
| TEİAŞ | İletim hatları ve trafolar | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS | Yıllık | Hatlar 1979-2024, trafolar 1980-2024 | `vi-enerji-nakil-hat-ve-trafolari-{year}` galerisi | HIGH | Gerilim kırılımı, uzunluk, trafo adet ve güç tabloları bulunur. |
| TEİAŞ | Güncel sistem özeti | https://www.teias.gov.tr/sayilarla-elektrik-iletimi | HTML | Aylık | Güncel | HTML | MEDIUM | Kurulu güç, hat, trafo, üretim, tüketim, ithalat ve ihracat özetidir; HTML son seçenek olarak kullanılabilir. |
| TEİAŞ | Yenilenebilir üretim ve kapasite payı | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLSX | Yıllık | 2000-2024 | kurulu güç ve üretim galerileri | HIGH | Yenilenebilir pay tabloları doğrudan yayınlanır. |
| TEİAŞ | EÜAŞ termik/hidrolik santral portföyü | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS | Yıllık | Güncel raporda 2024 | `i-kurulu-guc-2024` galerisi | HIGH | Metadata `source=TEİAŞ`, `subject=EÜAŞ` olarak döner. |
| TEİAŞ | EÜAŞ kurulu gücü ve üretimi | https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri | JSON katalog + XLS/XLSX | Yıllık | Kurulu güç 2006-2024; üretim 2014-2024 | üretici kuruluş tabloları | HIGH | Metadata `source=TEİAŞ`, `subject=EÜAŞ` olarak döner. |
| EÜAŞ | Santral portföyü ve santral detayları | https://www.euas.gov.tr/santraller | HTML | Düzensiz | Güncel | HTML sayfaları | MEDIUM | Alanlar santrale göre değişiyor; resmî site keşif sırasında aralıklı TLS reset/500 üretti. Production veri yolu olarak seçilmedi. |
| EÜAŞ | Kurulu güç/aylık üretim ana sayfa özeti | https://www.euas.gov.tr/ | HTML | Düzensiz | Güncel | HTML | NOT AVAILABLE | Sayfa 0 değerleri döndürebiliyor ve erişim kararsız; doğrulanmış veri kaynağı sayılmadı. |
| EÜAŞ | Yıllık/aylık üretim | https://www.euas.gov.tr/ | HTML/rapor | Belirsiz | Belirsiz | site araması | NOT AVAILABLE | Makinece okunabilir resmî seri veya doğrulanmış faaliyet raporu arşivi bulunamadı. |
| EÜAŞ | Operasyonel performans, duruş, bakım | https://www.euas.gov.tr/ | Belirlenemedi | Belirlenemedi | Belirlenemedi | — | NOT AVAILABLE | Açık ve doğrulanmış veri seti tespit edilmedi. |
| EÜAŞ | Yatırım/rehabilitasyon zaman serisi | https://www.euas.gov.tr/ | HTML/duyuru | Düzensiz | Belirsiz | site araması | NOT AVAILABLE | Tekil haberler var; bütünlüklü, karşılaştırılabilir açık veri seti yok. |

## Doğrulanmış erişim sözleşmeleri

- Sayfa içeriği: `GET https://www.teias.gov.tr/api/page?locale=tr-TR&slug={page_slug}`
- Galeri kataloğu: `GET https://www.teias.gov.tr/api/gallery?locale=tr-TR&slug={gallery_slug}`
- Dosya indirme: `GET https://webim.teias.gov.tr/file/{media.slug}?download`

Bu endpointler TEİAŞ sayfasının kendi JavaScript istemcisinde kullanılıyor ve keşif sırasında
JSON katalogları ile gerçek XLS/XLSX dosyaları indirilerek doğrulandı. Endpointler resmî fakat
belgelenmemiş web uygulaması sözleşmeleridir; şema değişikliği teknik risk olarak izlenmelidir.
