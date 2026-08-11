# turkiye-energy-mcp

Türkiye elektrik sistemine ilişkin doğrulanmış resmî kamu verilerini Model Context
Protocol (MCP) üzerinden sunan bağımsız Python sunucusu.

Kapsam yalnızca:

- TEİAŞ
- EÜAŞ konusu için TEİAŞ'ın yayımladığı resmî tablolar

EPİAŞ bilinçli olarak kapsam dışıdır. EÜAŞ konusu TEİAŞ kaynağından geldiğinde yanıt
`"source": "TEİAŞ", "subject": "EÜAŞ"` taşır. Kaynağı doğrulanamayan tool yayınlanmaz.

## Veri yaklaşımı

Sunucu, TEİAŞ web sitesinin kendi istemcisinin kullandığı resmî JSON galeri kataloğunu
okur ve katalogda listelenen XLS/XLSX dosyalarını indirir:

1. `https://www.teias.gov.tr/api/gallery`
2. `https://webim.teias.gov.tr/file/{media_slug}?download`

HTML scraping production veri yolunda kullanılmaz. EÜAŞ web sitesi keşif sırasında
aralıklı TLS reset/HTTP 500 ürettiğinden EÜAŞ HTML sayfaları production kaynağına
alınmamıştır. Ayrıntılı keşif ve uygulanamayan veri setleri:
[docs/data-discovery.md](docs/data-discovery.md).

## Mimari

```text
turkiye_energy_mcp/
├── server.py              # MCP tool kayıtları ve transport
├── service.py             # veri setleri ve kaynaklar arası hesaplamalar
├── clients/teias.py       # resmî JSON katalog/dosya istemcisi
├── parsers/               # Türkçe sayı/tarih ve XLS/XLSX ayrıştırıcıları
├── http_client.py         # ortak async connection pool, retry/backoff
├── cache.py               # eşzamanlı istek birleştiren TTL cache
├── models.py              # ortak response standardı
├── exceptions.py          # yapılandırılmış hata kodları
└── config.py              # environment ayarları
```

- Python 3.11+
- Resmî MCP Python SDK 2.x (`MCPServer`; FastMCP'nin güncel adı)
- `httpx.AsyncClient`
- Pydantic Settings
- pandas/openpyxl/xlrd
- async I/O, exponential backoff ve connection pooling
- Streamable HTTP ve stdio

## Kurulum

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

Geliştirme bağımlılıkları:

```bash
pip install -e '.[dev]'
pytest
```

## Çalıştırma

Streamable HTTP:

```bash
MCP_TRANSPORT=streamable-http HOST=0.0.0.0 PORT=8000 turkiye-energy-mcp
```

- MCP endpoint: `http://localhost:8000/mcp`
- Health endpoint: `http://localhost:8000/health`

stdio:

```bash
MCP_TRANSPORT=stdio turkiye-energy-mcp
```

## Environment variables

| Değişken | Varsayılan | Açıklama |
|---|---:|---|
| `HOST` | `0.0.0.0` | HTTP bind adresi |
| `PORT` | `8000` | HTTP portu; Railway otomatik sağlar |
| `LOG_LEVEL` | `INFO` | JSON log seviyesi |
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http` veya `stdio` |
| `MCP_PATH` | `/mcp` | Streamable HTTP yolu |
| `HTTP_TIMEOUT_SECONDS` | `30` | Kaynak timeout |
| `HTTP_MAX_RETRIES` | `3` | İlk deneme dışındaki retry sınırı |
| `HTTP_BACKOFF_SECONDS` | `0.5` | Exponential backoff çarpanı |
| `USER_AGENT` | proje user-agent'i | Resmî kaynağa gönderilen kimlik |
| `CACHE_DAILY_TTL_SECONDS` | `600` | Günlük veri cache süresi |
| `CACHE_MONTHLY_TTL_SECONDS` | `21600` | Aylık veri cache süresi |
| `CACHE_HISTORICAL_TTL_SECONDS` | `86400` | Yıllık veri cache süresi |
| `CACHE_PLANTS_TTL_SECONDS` | `86400` | Santral cache süresi |
| `TEIAS_BASE_URL` | resmî TEİAŞ URL | JSON katalog kökü |
| `TEIAS_FILE_BASE_URL` | resmî TEİAŞ dosya URL | Dosya indirme kökü |
| `PUBLIC_BASE_URL` | boş | Claude connector OAuth discovery origin'i (ör. Railway domain) |

Yıllık/aylık kaynak seçimi sabit yıl veya sabit dosya adına bağlı değildir.
Sunucu TEİAŞ galeri kataloğundan en yeni yayın tarihli ve en güncel dönemi kapsayan
resmî dosyayı otomatik seçer.

Tüm örnekler `.env.example` içindedir.

## Railway deploy

Depo `Dockerfile` ve `railway.toml` içerir.

```bash
railway up
```

Railway'de `MCP_TRANSPORT=streamable-http`, `HOST=0.0.0.0` ve
`PUBLIC_BASE_URL=https://<railway-domain>` kullanın. `PORT` Railway tarafından
atanır. Deploy sonrası Claude/Cursor endpoint:

```text
https://<railway-domain>/mcp
```

## MCP tool'ları

Toplam 16 production tool vardır.

| Tool | Parametreler | Veri |
|---|---|---|
| `teias_get_monthly_energy` | `start_date`, `end_date`, `metric?` | Aylık üretim, talep, ithalat/ihracat ve kaynak üretimi |
| `teias_get_generation` | `start_year`, `end_year`, `source?` | Yıllık kaynak bazlı brüt üretim |
| `teias_get_installed_capacity` | `start_year`, `end_year`, `source?` | Yıllık kaynak bazlı kurulu güç |
| `teias_get_peak_demand` | `start_year`, `end_year` | Ani/saatlik puant ve brüt talep |
| `teias_get_import_export` | `start_year`, `end_year`, `country?` | Toplam/ülke bazlı ithalat ve ihracat |
| `teias_get_transmission_statistics` | `start_year`, `end_year` | Gerilim bazlı hatlar ve trafolar |
| `teias_get_renewable_summary` | `start_year`, `end_year` | Yenilenebilir kapasite/üretim ve paylar |
| `teias_get_system_summary` | `year` | Yıllık elektrik sistemi özeti |
| `euas_get_power_plants` | `plant_type?`, `province?` | EÜAŞ hidro/termik portföyü |
| `euas_get_plant` | `plant_name` | Ada göre santral detayı |
| `euas_get_installed_capacity` | `start_year`, `end_year` | EÜAŞ kurulu güç serisi |
| `euas_get_generation` | `start_year`, `end_year`, `source?` | EÜAŞ yıllık üretimi |
| `euas_get_monthly_generation` | `start_date`, `end_date`, `source?` | EÜAŞ aylık üretimi |
| `get_euas_share_of_installed_capacity` | `start_year`, `end_year` | EÜAŞ/Türkiye kapasite payı |
| `get_euas_share_of_generation` | `start_year`, `end_year` | EÜAŞ/Türkiye üretim payı |
| `compare_euas_vs_turkey_generation` | `start_year`, `end_year` | İki üretim serisinin karşılaştırması |

Tarih biçimi `YYYY-MM-DD`, timezone `Europe/Istanbul`'dur. Yıllık tool'lar tam
yıl alır. Güç `MW`, enerji `GWh`, trafo kapasitesi `MVA`, hat uzunluğu `km` olarak
açık alan adlarıyla döner.

## Response standardı

```json
{
  "source": "TEİAŞ",
  "subject": "EÜAŞ",
  "dataset": "installed_capacity",
  "start_date": "2020",
  "end_date": "2024",
  "unit": "MW",
  "data": [],
  "metadata": {
    "retrieved_at": "2026-08-11T13:00:00+03:00",
    "source_url": "https://webim.teias.gov.tr/file/...",
    "source_format": "xls",
    "frequency": "annual",
    "notes": null,
    "original_unit": "MW",
    "latest_available_period": "2024",
    "data_freshness": "current",
    "publication_date": "2026-01-15T12:06:51+00:00",
    "selected_source_name": "13-Yıllar İtibariyle ... (2006-2024).xls"
  }
}
```

`data_freshness` değerleri: `current`, `provisional`, `historical`, `partial`,
`unavailable`. İstenen güncel dönem yoksa eski veri sessizce güncel gibi
döndürülmez; `latest_available_period` ve `data_freshness` açıkça verilir veya
hata detayında yer alır.

Hatalar traceback yerine:

```json
{
  "error": true,
  "code": "DATA_NOT_AVAILABLE",
  "message": "Belirtilen dönem için veri bulunamadı.",
  "source": "TEİAŞ",
  "details": {
    "latest_available_period": "2024",
    "data_freshness": "unavailable",
    "requested_period": "2027"
  }
}
```

Kodlar: `DATA_NOT_AVAILABLE`, `SOURCE_UNAVAILABLE`, `PARSING_ERROR`,
`INVALID_PARAMETER`, `RATE_LIMITED`, `AUTH_REQUIRED`.

## Cursor config

Remote Streamable HTTP (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "turkiye-energy": {
      "url": "https://<railway-domain>/mcp"
    }
  }
}
```

Local stdio:

```json
{
  "mcpServers": {
    "turkiye-energy": {
      "command": "/absolute/path/turkiye-energy-mcp/.venv/bin/turkiye-energy-mcp",
      "env": {
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Claude config

Claude custom connector (claude.ai / Desktop Connect UI) için MCP URL **mutlaka**
`/mcp` ile bitmeli:

```text
https://turkiye-energy-mcp-production.up.railway.app/mcp
```

Kök domain (`...railway.app`) 404 döner; Claude bunu “sign-in service” hatası olarak
gösterebilir. Railway Variables içine şunu ekleyin ve redeploy edin:

```text
PUBLIC_BASE_URL=https://turkiye-energy-mcp-production.up.railway.app
MCP_TRANSPORT=streamable-http
HOST=0.0.0.0
```

`PUBLIC_BASE_URL` Claude'un beklediği OAuth discovery / Dynamic Client Registration
uçlarını açar. MCP araçları yine herkese açık kalır; OAuth yalnızca bağlayıcı
uyumluluğu içindir.

Claude Code remote HTTP:

```bash
claude mcp add --transport http turkiye-energy https://turkiye-energy-mcp-production.up.railway.app/mcp
```

Claude Desktop local stdio config:

```json
{
  "mcpServers": {
    "turkiye-energy": {
      "command": "/absolute/path/turkiye-energy-mcp/.venv/bin/turkiye-energy-mcp",
      "env": {
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Örnek promptlar

- “2024 yılında Türkiye'nin toplam elektrik üretimi ne kadardı?”
- “2024 sonunda Türkiye'nin kaynak bazında kurulu gücünü getir.”
- “2006-2024 arasında Türkiye'nin rüzgâr kurulu gücü nasıl değişti?”
- “EÜAŞ hidroelektrik santrallerini kurulu güçlerine göre sırala.”
- “EÜAŞ'ın Türkiye toplam elektrik üretimindeki payını 2014-2024 için göster.”
- “Türkiye'nin Bulgaristan ve Yunanistan ile elektrik ithalat/ihracatını karşılaştır.”
- “Son beş kesinleşmiş yıldaki puant talep büyümesini hesapla.”

## Resmî kaynaklar ve tarihsel derinlik

| Veri seti | Resmî sayfa | Sıklık | Derinlik |
|---|---|---|---|
| Aylık üretim/talep | [Aylık raporlar](https://www.teias.gov.tr/aylik-elektrik-uretim-tuketim-raporlari) | Aylık | 2019-günümüz (katalogdan dinamik) |
| Kaynak bazlı üretim | [Yıllık istatistikler](https://www.teias.gov.tr/turkiye-elektrik-uretim-iletim-istatistikleri) | Yıllık | En güncel yıllık galerideki seri |
| Kaynak bazlı kurulu güç | aynı | Yıllık | En güncel yıllık galerideki seri |
| Puant/talep | aynı | Yıllık | En güncel yıllık galerideki seri |
| Dış ticaret/ülkeler | aynı | Yıllık | En güncel yıllık denge tablosu |
| İletim hatları | aynı | Yıllık | En güncel yıllık galerideki seri |
| Trafolar | aynı | Yıllık | En güncel yıllık galerideki seri |
| EÜAŞ santral portföyü | aynı, EÜAŞ konulu TEİAŞ tabloları | Yıllık | En güncel yıllık rapor dönemi |
| EÜAŞ kurulu güç | aynı | Yıllık | En güncel yıllık galerideki seri |
| EÜAŞ üretim | aynı | Yıllık | En güncel yıllık galerideki seri |

## Test ve smoke test

Ana testler internet kullanmaz:

```bash
pytest
```

Gerçek resmî endpoint ve 16 production veri yolu:

```bash
python scripts/smoke_test.py
```

Smoke test internet gerektirir ve başarısız tool varsa non-zero exit code döndürür.

## Bilinen sınırlamalar

- TEİAŞ JSON galeri endpointi resmî sitenin kullandığı fakat belgelenmemiş bir web
  uygulaması sözleşmesidir; şema/slug değişikliği riski vardır.
- Yıllık ve aylık dosyalar sabit yıla kilitlenmez; en yeni yayın ve en güncel dönem
  otomatik seçilir. `latest_available_period` ve `data_freshness` her yanıtta yer alır.
- Cari aylık değerler TEİAŞ tarafından geçici olabilir (`provisional`).
- Saatlik tüketim ve günlük yük eğrisi için kararlı, doğrulanmış makine endpointi
  bulunmadığından tool yoktur.
- Ülke bazlı dış ticaret, en güncel yıllık denge çalışma kitabının kapsadığı yıllarla
  sınırlıdır.
- EÜAŞ'ın kendi sitesi kararsız olduğundan veri yolu değildir. EÜAŞ tool'ları TEİAŞ'ın
  EÜAŞ konulu resmî tablolarını kullanır.
- EÜAŞ santral çalışma kitabındaki güvenilir olmayan ünite/devreye giriş hücreleri
  yayımlanmaz.
- EÜAŞ operasyonel performans, duruş, bakım ve bütünlüklü yatırım serileri için
  doğrulanmış açık veri bulunmadığından tool yayınlanmaz.
