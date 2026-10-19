import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from .cache import AsyncTTLCache
from .clients.teias import TeiasClient
from .config import get_settings
from .exceptions import EnergyDataError, ErrorCode
from .http_client import ResilientHTTPClient
from .logging import configure_logging
from .oauth_compat import PublicOAuthCompat
from .service import EnergyService
from .ytbs.repository import YtbsRepository
from .ytbs.service import YtbsService

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

cache = AsyncTTLCache()
http = ResilientHTTPClient(settings)
teias_client = TeiasClient(settings, http, cache)
service = EnergyService(teias_client)
ytbs = YtbsService(YtbsRepository(settings, http, cache))
oauth: PublicOAuthCompat | None = None
if settings.public_base_url:
    oauth = PublicOAuthCompat(settings.public_base_url, settings.mcp_path)


@asynccontextmanager
async def lifespan(_: MCPServer):
    yield {"service": service}
    await http.close()


mcp = MCPServer(
    name="turkiye-energy-mcp",
    title="Türkiye Energy MCP",
    version="0.1.0",
    description="TEİAŞ resmî verileri ve TEİAŞ'ın EÜAŞ konulu resmî tabloları.",
    instructions=(
        "EPİAŞ bu sunucunun kapsamı dışındadır. Yanıtlardaki source/subject ve birim "
        "alanlarını koruyun; MW güç, GWh enerji değeridir. ytbs_* tool'ları TEİAŞ YTBS "
        "geçici işletme verisidir (lisanssız üretim dahil, geriye dönük revize olur); resmî "
        "aylık/yıllık istatistik için teias_* tool'larını kullanın."
    ),
    log_level=settings.log_level,
    lifespan=lifespan,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "turkiye-energy-mcp"})


if oauth is not None:

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request) -> Any:
        return await oauth.root_help(request)

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def oauth_as_metadata(_: Request) -> JSONResponse:
        return JSONResponse(oauth.authorization_server_metadata())

    @mcp.custom_route("/.well-known/openid-configuration", methods=["GET"])
    async def openid_configuration(_: Request) -> JSONResponse:
        return JSONResponse(oauth.authorization_server_metadata())

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    async def oauth_pr_metadata(_: Request) -> JSONResponse:
        return JSONResponse(oauth.protected_resource_metadata())

    @mcp.custom_route(f"/.well-known/oauth-protected-resource{settings.mcp_path}", methods=["GET"])
    async def oauth_pr_metadata_with_path(_: Request) -> JSONResponse:
        return JSONResponse(oauth.protected_resource_metadata())

    @mcp.custom_route("/.well-known/jwks.json", methods=["GET"])
    async def jwks(request: Request) -> Any:
        return await oauth.jwks(request)

    @mcp.custom_route("/register", methods=["POST"])
    async def register(request: Request) -> Any:
        return await oauth.register(request)

    @mcp.custom_route("/authorize", methods=["GET"])
    async def authorize(request: Request) -> Any:
        return await oauth.authorize(request)

    @mcp.custom_route("/token", methods=["POST"])
    async def token(request: Request) -> Any:
        return await oauth.token(request)


async def _safe(awaitable: Any) -> dict[str, Any]:
    try:
        return await awaitable
    except EnergyDataError as exc:
        return exc.as_dict()
    except Exception:
        logger.exception("Unhandled tool error")
        return EnergyDataError(
            ErrorCode.PARSING_ERROR,
            "Veri işlenirken beklenmeyen bir hata oluştu.",
        ).as_dict()


@mcp.tool(title="TEİAŞ aylık enerji verisi", annotations=READ_ONLY)
async def teias_get_monthly_energy(
    start_date: str,
    end_date: str,
    metric: str | None = None,
) -> dict[str, Any]:
    """Aylık üretim, brüt talep, ithalat/ihracat veya kaynak üretimini getirir.

    Tarihler YYYY-MM-DD biçimindedir. metric örnekleri: tüketim, üretim, rüzgar,
    güneş, hidro, doğal gaz, ithalat, ihracat.
    """
    return await _safe(service.get_monthly_energy(start_date, end_date, metric))


@mcp.tool(title="TEİAŞ yıllık kaynak bazlı üretim", annotations=READ_ONLY)
async def teias_get_generation(
    start_year: int,
    end_year: int,
    source: str | None = None,
) -> dict[str, Any]:
    """Türkiye yıllık brüt elektrik üretimini kaynak bazında GWh olarak getirir."""
    return await _safe(service.get_generation(start_year, end_year, source))


@mcp.tool(title="TEİAŞ kurulu güç", annotations=READ_ONLY)
async def teias_get_installed_capacity(
    start_year: int,
    end_year: int,
    source: str | None = None,
) -> dict[str, Any]:
    """Türkiye yıllık kurulu gücünü kaynak bazında MW olarak getirir."""
    return await _safe(service.get_installed_capacity(start_year, end_year, source))


@mcp.tool(title="TEİAŞ puant talep", annotations=READ_ONLY)
async def teias_get_peak_demand(start_year: int, end_year: int) -> dict[str, Any]:
    """Yıllık ani/saatlik puant, kurulu güç ve brüt talep serisini getirir."""
    return await _safe(service.get_peak_demand(start_year, end_year))


@mcp.tool(title="TEİAŞ ithalat ve ihracat", annotations=READ_ONLY)
async def teias_get_import_export(
    start_year: int,
    end_year: int,
    country: str | None = None,
) -> dict[str, Any]:
    """Yıllık elektrik ithalat/ihracatını toplam veya ülke bazında GWh getirir."""
    return await _safe(service.get_import_export(start_year, end_year, country))


@mcp.tool(title="TEİAŞ iletim istatistikleri", annotations=READ_ONLY)
async def teias_get_transmission_statistics(
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Gerilim bazlı hat uzunluğu ve trafo adet/kapasite serilerini getirir."""
    return await _safe(service.get_transmission_statistics(start_year, end_year))


@mcp.tool(title="TEİAŞ yenilenebilir enerji özeti", annotations=READ_ONLY)
async def teias_get_renewable_summary(
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Yenilenebilir kurulu güç/üretim ve toplam içindeki paylarını hesaplar."""
    return await _safe(service.get_renewable_summary(start_year, end_year))


@mcp.tool(title="Türkiye elektrik sistemi özeti", annotations=READ_ONLY)
async def teias_get_system_summary(year: int) -> dict[str, Any]:
    """Bir yıl için kapasite, üretim, talep, dış ticaret ve puant özetini getirir."""
    return await _safe(service.get_system_summary(year))


@mcp.tool(title="EÜAŞ santral portföyü", annotations=READ_ONLY)
async def euas_get_power_plants(
    plant_type: str | None = None,
    province: str | None = None,
) -> dict[str, Any]:
    """TEİAŞ'ın EÜAŞ konulu resmî tablolarından termik/hidro santralleri getirir."""
    return await _safe(service.get_euas_power_plants(plant_type, province))


@mcp.tool(title="EÜAŞ santral detayı", annotations=READ_ONLY)
async def euas_get_plant(plant_name: str) -> dict[str, Any]:
    """Ad eşleşmesiyle bir veya daha fazla EÜAŞ santral kaydını getirir."""
    return await _safe(service.get_euas_plant(plant_name))


@mcp.tool(title="EÜAŞ kurulu güç", annotations=READ_ONLY)
async def euas_get_installed_capacity(
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """TEİAŞ kaynağından EÜAŞ yıllık termik, yenilenebilir ve toplam gücünü getirir."""
    return await _safe(service.get_euas_installed_capacity(start_year, end_year))


@mcp.tool(title="EÜAŞ yıllık üretim", annotations=READ_ONLY)
async def euas_get_generation(
    start_year: int,
    end_year: int,
    source: str | None = None,
) -> dict[str, Any]:
    """TEİAŞ kaynağından EÜAŞ yıllık üretimini toplam veya kaynak bazında getirir."""
    return await _safe(service.get_euas_generation(start_year, end_year, source))


@mcp.tool(title="EÜAŞ aylık üretim", annotations=READ_ONLY)
async def euas_get_monthly_generation(
    start_date: str,
    end_date: str,
    source: str | None = None,
) -> dict[str, Any]:
    """TEİAŞ aylık raporlarından EÜAŞ üretimini kaynak bazında GWh getirir."""
    return await _safe(service.get_euas_monthly_generation(start_date, end_date, source))


@mcp.tool(title="EÜAŞ'ın Türkiye kurulu gücündeki payı", annotations=READ_ONLY)
async def get_euas_share_of_installed_capacity(
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Aynı TEİAŞ raporundaki girdilerden EÜAŞ kapasite payını hesaplar."""
    return await _safe(service.get_euas_capacity_share(start_year, end_year))


@mcp.tool(title="EÜAŞ'ın Türkiye üretimindeki payı", annotations=READ_ONLY)
async def get_euas_share_of_generation(
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Aynı TEİAŞ tablosundaki girdilerden EÜAŞ üretim payını hesaplar."""
    return await _safe(service.get_euas_generation_share(start_year, end_year))


@mcp.tool(title="EÜAŞ ve Türkiye üretim karşılaştırması", annotations=READ_ONLY)
async def compare_euas_vs_turkey_generation(
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """EÜAŞ ve Türkiye yıllık üretimini ve EÜAŞ payını karşılaştırır."""
    return await _safe(service.compare_euas_vs_turkey_generation(start_year, end_year))



@mcp.tool(title="YTBS 15 dk yük ve kaynak bazlı üretim", annotations=READ_ONLY)
async def ytbs_get_load_generation_15min(
    start_date: str,
    end_date: str,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """TEİAŞ YTBS 15 dakikalık anlık yük (MW) ve 18 kaynağa göre üretim (MW); lisanssız dahil.

    En fazla 7 gün. sources örnekleri: rüzgar, güneş, doğalgaz, barajlı, ithal kömür.
    yuk_mw anlık talep değeridir; enerji (MWh) için ytbs_get_hourly_balance kullanın.
    """
    return await _safe(ytbs.get_load_generation_15min(start_date, end_date, sources))


@mcp.tool(title="YTBS saatlik enerji dengesi", annotations=READ_ONLY)
async def ytbs_get_hourly_balance(start_date: str, end_date: str) -> dict[str, Any]:
    """Saatlik yenilenemez/yenilenebilir üretim, depolama şarj/deşarj, ithalat, ihracat ve
    tüketim (MWh). En fazla 92 gün."""
    return await _safe(ytbs.get_hourly_balance(start_date, end_date))


@mcp.tool(title="YTBS günlük üretim dağılımı", annotations=READ_ONLY)
async def ytbs_get_daily_generation(
    start_date: str,
    end_date: str,
    breakdown: str = "kaynak",
    source: str | None = None,
) -> dict[str, Any]:
    """Günlük üretim (MWh), breakdown='kaynak' veya 'kurulus' (EÜAŞ, lisanssız, serbest
    üretim vb.). En fazla 366 gün; source yalnız kaynak kırılımında filtreler."""
    return await _safe(ytbs.get_daily_generation(start_date, end_date, breakdown, source))


@mcp.tool(title="YTBS günlük kurulu güç", annotations=READ_ONLY)
async def ytbs_get_installed_capacity(
    on_date: str | None = None,
    breakdown: str = "kaynak",
) -> dict[str, Any]:
    """Kurulu güç (MW) ve santral sayısı; istenen tarihe en yakın önceki günün anlık
    görüntüsü. breakdown='kaynak', 'kurulus' veya 'detay' (kuruluş × kaynak)."""
    return await _safe(ytbs.get_installed_capacity(on_date, breakdown))


@mcp.tool(title="YTBS saatlik emre amade kapasite", annotations=READ_ONLY)
async def ytbs_get_available_capacity(
    start_date: str,
    end_date: str,
    items: list[str] | None = None,
) -> dict[str, Any]:
    """Kaynak bazlı saatlik emre amade kapasite, TEİAŞ talep tahmini, emre amadelik ve arz
    güvenliği oranı. En fazla 31 gün. items örnekleri: rüzgar, toplam, taleptahmin,
    arzguvenligiorani."""
    return await _safe(ytbs.get_available_capacity(start_date, end_date, items))


@mcp.tool(title="YTBS ulusal ve bölgesel puant", annotations=READ_ONLY)
async def ytbs_get_peaks(
    start_date: str,
    end_date: str,
    scope: str = "ulusal",
) -> dict[str, Any]:
    """scope='ulusal': günlük/aylık/yıllık maks-min anlık talep, saatlik ve günlük tüketim
    rekorları; scope='bolgesel': 9 Yük Tevzi bölgesinin günlük puantı. En fazla 92 gün."""
    return await _safe(ytbs.get_peaks(start_date, end_date, scope))


@mcp.tool(title="YTBS veri deposu durumu", annotations=READ_ONLY)
async def ytbs_get_sync_status() -> dict[str, Any]:
    """ytbs-sync deposunun son senkron zamanı, kapsadığı günler ve son hatalar."""
    return await _safe(ytbs.get_sync_status())


def main() -> None:
    if settings.mcp_transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=settings.host,
            port=settings.port,
            streamable_http_path=settings.mcp_path,
            stateless_http=True,
            json_response=True,
        )


if __name__ == "__main__":
    main()
