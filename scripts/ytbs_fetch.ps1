<#
.SYNOPSIS
  TEİAŞ YTBS "İstatistikler" sayfasından 15 dakikalık yük eğrisini ve kaynak bazlı
  üretimi (MW) gün gün çeker ve CSV olarak kaydeder.

.DESCRIPTION
  Kaynak: https://ytbsbilgi.teias.gov.tr/ytbsbilgi/frm_istatistikler.jsf
  Sayfa veriyi gömülü JSON olarak taşır (gunlukYukEgrisiData, gunlukUretimEgrisiData,
  santralKaynakListesi). Geçmiş günler, sayfadaki "Göster" butonunun PrimeFaces AJAX
  POST'u taklit edilerek alınır.

  ÖNEMLİ:
  - YTBS yalnızca Türkiye IP'lerinden erişilebiliyor (bulut/yurt dışı IP'lerde TLS reset).
  - Veriler TEİAŞ'ın kendi ifadesiyle geçici işletme verisidir; geriye dönük düzeltmeye
    açıktır, lisanssız üretim ay sonuna kadar tahmini değerdir. Resmî aylık/yıllık
    TEİAŞ istatistiklerinin yerine geçmez.
  - Değerler MW'dır. 15 dk değerinin ortalama mı anlık mı olduğu sayfada belirtilmez;
    MWh'e çevirmeden önce doğrulayın.
  - Henüz yayımlanmamış zaman dilimleri (yük ve toplam üretim 0) CSV'de boş bırakılır.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\ytbs_fetch.ps1 -Baslangic 2026-10-18
  powershell -ExecutionPolicy Bypass -File .\ytbs_fetch.ps1 -Baslangic 2026-10-01 -Bitis 2026-10-07 -Excel
#>
param(
    [Parameter(Mandatory = $true)][string]$Baslangic,   # yyyy-MM-dd
    [string]$Bitis,                                      # yyyy-MM-dd; boşsa Baslangic
    [string]$CiktiKlasoru = (Join-Path $HOME 'Desktop\ytbs'),
    [int]$BeklemeSn = 2,                                 # günler arası nezaket beklemesi
    [switch]$Excel                                       # sayfanın günlük Excel raporunu da indir
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Base = 'https://ytbsbilgi.teias.gov.tr'
$PageUrl = "$Base/ytbsbilgi/frm_istatistikler.jsf"
$Inv = [Globalization.CultureInfo]::InvariantCulture
$UserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ytbs_fetch.ps1'

function Get-Utf8Text($resp) {
    [Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
}

function Get-FirstMatch([string]$text, [string]$pattern, [string]$what) {
    $m = [regex]::Match($text, $pattern, 'Singleline')
    if (-not $m.Success) { throw "Sayfada bulunamadı: $what" }
    $m.Groups[1].Value
}

function Get-JsArray([string]$text, [string]$name) {
    $m = [regex]::Match($text, "var\s+$name\s*=\s*(\[.*?\]);", 'Singleline')
    if (-not $m.Success) { return $null }
    , ($m.Groups[1].Value | ConvertFrom-Json)
}

function Format-Num($v) {
    if ($null -eq $v) { return '' }
    ([double]$v).ToString('0.###', $Inv)
}

# Her gün için temiz bir sayfa oturumu açar: çerez + ViewState + form alanları.
function Open-Page($session) {
    $resp = Invoke-WebRequest -Uri $PageUrl -WebSession $session -UseBasicParsing -UserAgent $UserAgent
    $html = Get-Utf8Text $resp
    $action = Get-FirstMatch $html 'id="formdash"[^>]*action="([^"]+)"' 'formdash action'
    $images = [regex]::Matches($html, 'type="image" src="[^"]*excel\.png[^"]*" name="([^"]+)"')
    [pscustomobject]@{
        Html       = $html
        PostUrl    = $Base + [Net.WebUtility]::HtmlDecode($action)
        ViewState  = Get-FirstMatch $html 'id="j_id1:jakarta\.faces\.ViewState:1" value="([^"]+)"' 'ViewState'
        Hidden1    = Get-FirstMatch $html 'name="hidden1" value="([^"]*)"' 'hidden1'
        Yil        = Get-FirstMatch $html '<option value="(\d{4})" selected' 'seçili yıl'
        GunlukExcel = if ($images.Count -ge 1) { $images[0].Groups[1].Value } else { $null }
    }
}

function Get-FormBody($page, [datetime]$day) {
    @{
        'formdash'                    = 'formdash'
        'hidden1'                     = $page.Hidden1
        'formdash:bitisTarihi2_input' = $day.ToString('dd-MM-yyyy', $Inv)
        'formdash:yilsecim_focus'     = ''
        'formdash:yilsecim_input'     = $page.Yil
        'jakarta.faces.ViewState'     = $page.ViewState
    }
}

function Invoke-FormPost($session, $page, $body, $headers) {
    $resp = Invoke-WebRequest -Uri $page.PostUrl -Method Post -Body $body -Headers $headers `
        -ContentType 'application/x-www-form-urlencoded; charset=UTF-8' `
        -WebSession $session -UseBasicParsing -UserAgent $UserAgent
    Get-Utf8Text $resp
}

function Read-DayData([string]$text, $page, [string]$want) {
    $yuk = Get-JsArray $text 'gunlukYukEgrisiData'
    $uretim = Get-JsArray $text 'gunlukUretimEgrisiData'
    if ($null -eq $yuk -or $null -eq $uretim) { return $null }
    $kaynaklar = Get-JsArray $text 'santralKaynakListesi'
    if ($null -eq $kaynaklar) { $kaynaklar = Get-JsArray $page.Html 'santralKaynakListesi' }
    if (-not ($yuk[0].zaman -like "$want*")) {
        throw "Sunucu istenen günü döndürmedi: istenen $want, gelen $($yuk[0].zaman)"
    }
    [pscustomobject]@{ Page = $page; Yuk = $yuk; Uretim = $uretim; Kaynaklar = $kaynaklar }
}

function Save-Debug([string]$text, [string]$name) {
    $path = Join-Path $CiktiKlasoru $name
    [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false))
    $path
}

# Önce sayfanın kendi AJAX çağrısı denenir; veri gelmezse normal (tam sayfa) form POST'u.
function Get-DayData($session, [datetime]$day) {
    $want = $day.ToString('yyyy-MM-dd', $Inv)

    $page = Open-Page $session
    $body = Get-FormBody $page $day
    $body['jakarta.faces.partial.ajax'] = 'true'
    $body['jakarta.faces.source'] = 'formdash:gunlukRapor'
    $body['jakarta.faces.partial.execute'] = '@all'
    $body['jakarta.faces.partial.render'] = 'formdash'
    $body['formdash:gunlukRapor'] = 'formdash:gunlukRapor'
    $headers = @{ 'Faces-Request' = 'partial/ajax'; 'X-Requested-With' = 'XMLHttpRequest' }
    $ajaxText = Invoke-FormPost $session $page $body $headers
    $data = Read-DayData $ajaxText $page $want
    if ($data) { return $data }
    $ajaxDebug = Save-Debug $ajaxText "ytbs_debug_ajax_$want.xml"

    $page = Open-Page $session
    $body = Get-FormBody $page $day
    $body['formdash:gunlukRapor'] = 'formdash:gunlukRapor'
    $fullText = Invoke-FormPost $session $page $body @{}
    $data = Read-DayData $fullText $page $want
    if ($data) { return $data }
    $fullDebug = Save-Debug $fullText "ytbs_debug_full_$want.html"

    throw "Veri bulunamadı. Hata ayıklama dosyaları: $ajaxDebug ; $fullDebug"
}

function Save-DailyExcel($session, $page, [datetime]$day, [string]$dir) {
    if (-not $page.GunlukExcel) { Write-Warning 'Excel butonu bulunamadı.'; return }
    $body = Get-FormBody $page $day
    $body["$($page.GunlukExcel).x"] = '10'
    $body["$($page.GunlukExcel).y"] = '10'
    $resp = Invoke-WebRequest -Uri $page.PostUrl -Method Post -Body $body `
        -ContentType 'application/x-www-form-urlencoded; charset=UTF-8' `
        -WebSession $session -UseBasicParsing -UserAgent $UserAgent
    $bytes = $resp.RawContentStream.ToArray()
    $ext = if ($bytes.Length -ge 2 -and $bytes[0] -eq 0x50 -and $bytes[1] -eq 0x4B) { 'xlsx' }
           elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xD0 -and $bytes[1] -eq 0xCF) { 'xls' }
           else { 'bin' }
    $path = Join-Path $dir ("ytbs_gunluk_{0}.{1}" -f $day.ToString('yyyy-MM-dd', $Inv), $ext)
    [IO.File]::WriteAllBytes($path, $bytes)
    if ($ext -eq 'bin') {
        Write-Warning "Excel yerine beklenmeyen içerik geldi ($($resp.Headers['Content-Type'])): $path"
    }
}

# --- Ana akış ---
$start = [datetime]::ParseExact($Baslangic, 'yyyy-MM-dd', $Inv)
$end = if ($Bitis) { [datetime]::ParseExact($Bitis, 'yyyy-MM-dd', $Inv) } else { $start }
if ($end -lt $start) { throw 'Bitis, Baslangic tarihinden önce olamaz.' }
New-Item -ItemType Directory -Force -Path $CiktiKlasoru | Out-Null

$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$lines = New-Object System.Collections.Generic.List[string]
$kodlar = $null
$hatalar = @()

for ($day = $start; $day -le $end; $day = $day.AddDays(1)) {
    $gun = $day.ToString('yyyy-MM-dd', $Inv)
    try {
        $d = Get-DayData $session $day
        if ($null -eq $kodlar) {
            $kodlar = @($d.Kaynaklar | ForEach-Object { $_.kod })
            $lines.Add((@('zaman', 'yuk_mw') + ($kodlar | ForEach-Object { "${_}_mw" }) + @('toplam_uretim_mw')) -join ',')
        }
        $uretimByZaman = @{}
        foreach ($u in $d.Uretim) { $uretimByZaman[$u.zaman] = $u }

        $dolu = 0; $peakYuk = 0; $maxRuzgar = 0; $maxGunes = 0
        foreach ($y in $d.Yuk) {
            # Yük ve üretim ayrı ayrı yayımlanabiliyor; 0 = henüz yayımlanmadı -> boş hücre.
            $u = $uretimByZaman[$y.zaman]
            $yukVar = ([double]$y.deger -ne 0)
            $uretimVar = ($u -and [double]$u.toplam -ne 0)
            $row = @($y.zaman)
            if ($yukVar) {
                $dolu++
                $peakYuk = [Math]::Max($peakYuk, [double]$y.deger)
                $row += Format-Num $y.deger
            } else {
                $row += ''
            }
            if ($uretimVar) {
                $maxRuzgar = [Math]::Max($maxRuzgar, [double]$u.ruzgar)
                $maxGunes = [Math]::Max($maxGunes, [double]$u.gunes)
                foreach ($k in $kodlar) { $row += Format-Num $u.$k }
                $row += Format-Num $u.toplam
            } else {
                $row += @('') * ($kodlar.Count + 1)
            }
            $lines.Add($row -join ',')
        }
        Write-Host ("{0}: {1}/{2} dolu nokta | tepe yük {3:N0} MW | maks rüzgar {4:N0} MW | maks güneş {5:N0} MW" -f `
                $gun, $dolu, $d.Yuk.Count, $peakYuk, $maxRuzgar, $maxGunes)

        if ($Excel) { Save-DailyExcel $session $d.Page $day $CiktiKlasoru }
    } catch {
        Write-Warning "${gun}: $($_.Exception.Message)"
        $hatalar += $gun
    }
    if ($day -lt $end) { Start-Sleep -Seconds $BeklemeSn }
}

if ($lines.Count -gt 0) {
    $csv = Join-Path $CiktiKlasoru ("ytbs_15dk_{0}_{1}.csv" -f $start.ToString('yyyy-MM-dd', $Inv), $end.ToString('yyyy-MM-dd', $Inv))
    [IO.File]::WriteAllLines($csv, $lines, (New-Object Text.UTF8Encoding $true))
    Write-Host "CSV: $csv"
}
if ($hatalar.Count -gt 0) { Write-Warning ("Alınamayan günler: " + ($hatalar -join ', ')) }
