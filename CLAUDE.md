# CLAUDE.md — qgis-property-report

Kaynak gerçeklik `docs/` altındaki dosyalardır (`01_PROJECT_BRIEF.md`, `02_ARCHITECTURE.md`, `03_ROADMAP.md`). Bu dosya onların özetidir; çelişki varsa önce `docs/`'u güncellemeyi öner.

## Proje amacı
**Property Location Report Generator** — Türkiye'de bir adres veya koordinat alır, çevresindeki önemli noktaları (OSM) analiz eder, headless QGIS ile haritalı bir PDF konum raporu üretir ve LLM ile 3–4 cümlelik konum özeti yazar.

- Hedef: Upwork STR/gayrimenkul nişi için portföy parçası + GIS remote iş başvurularında referans.
- Hedef kitle: Airbnb/Booking host'ları, property manager'lar, emlak ajansları, yatırımcılar.
- Sofistike değil; **çalışan, temiz, iyi belgelenmiş, görsel olarak güçlü** olması yeterli.
- **Kapsam: yalnızca Türkiye** (karar: 2026-11-14). README'de "Coverage: Türkiye only" açıkça yazılır. Türkiye dışı girdi net hatayla reddedilir.
- **Kapsam dışı:** web arayüzü/dashboard, kullanıcı hesapları/ödeme, Türkiye dışı adresler/çok ülkeli veri, gerçek zamanlı/canlı POI verisi, üçüncü taraf POI servisleri (Overpass, Google Places vb.). Kapsam genişlemesi fark edersen belirt.

## Mimari
```
[Google Sheet / Webhook]
        │
        ▼
     [ n8n ]  ── orkestrasyon
        ├─► Nominatim (geocode)
        ├─► Python/PyQGIS container (POI: yerel Geofabrik Türkiye extract'ı → analiz + harita PDF)
        ├─► LLM API (konum özeti)
        └─► Google Drive / Gmail (teslimat, Sheet'e link)
```

| Katman | Seçim |
|---|---|
| GIS motoru | QGIS / PyQGIS (headless) |
| Konteyner | `qgis/qgis` Docker image (GDAL/`ogr2ogr` OSM sürücüsüyle hazır) |
| Orkestrasyon | n8n |
| Geocoding | Nominatim |
| POI verisi | Yerel Geofabrik `turkey-latest.osm.pbf` + `ogr2ogr -spat` |
| AI | Mevcut LLM API (Groq / OpenAI / Claude) |
| Veri formatı | GeoPackage (.gpkg) |

Araç seçim ilkesi: büyük community desteği olan araçlar. Zayıf community'li alternatif yalnızca belirgin avantajı varsa.

### POI veri kaynağı (özet)
- **Overpass bırakıldı** (12–14 Kasım 2026: public sunucular 406/502/timeout; ev ağı ve hotspot'ta aynı sonuç). Overpass kodu, endpoint listesi, backoff, `OVERPASS_*` env değişkenleri kaldırıldı (2026-11-14); Overpass'a geri dönme.
- Dosya `data/turkey-latest.osm.pbf` (~620 MB), repoya girmez (`.gitignore`), Docker'a volume ile bağlanır, yol `OSM_PBF_PATH`.
- Doğrulanmış komut (PowerShell): `docker run --rm -v "${PWD}:/data" qgis/qgis ogr2ogr -f GeoJSON /data/out.geojson /data/turkey-latest.osm.pbf points -spat xmin ymin xmax ymax` (`-spat` sırası: lon_min lat_min lon_max lat_max). Antalya merkezi (36.887, 30.704) ±0.0113° → 1852 nokta, 705 amenity, ~7 sn.
- **Yalnızca `points` yetmez:** plaj/park/okul/çoğu mağaza poligon; `multipolygons` da okunur (poligon olarak kalır, mesafe kenara). Etiketler `other_tags` içinde (`"amenity"=>"..."`).
- Eski test noktası 36.8, 30.7 denizde (1 km kutuda 0 nokta); kullanma.
- Detay: `docs/02_ARCHITECTURE.md` → "POI veri kaynağı".

### PyQGIS script arayüzü
```
python generate_report.py --lat 36.887 --lon 30.704 --name "Villa Demo" --out /out
```
- Girdi: koordinat + isim + (opsiyonel) POI JSON (verilmezse `poi_provider` pbf'ten okur)
- Çıktı: `report.pdf`, `analysis.gpkg`, `summary.json` (mesafe tablosu)

### Analiz adımları
1. Mülk noktasını EPSG:4326 → yerel UTM'e dönüştür
2. 500 m ve 1000 m buffer üret
3. POI katmanlarını buffer ile kes, kategoriye göre say
4. Kategori başına en yakın POI mesafesini hesapla
5. Katmanları stille (`.qml`), OSM XYZ basemap ekle
6. Print layout (`.qpt`): harita, başlık, mesafe tablosu, AI özeti, kaynak notu
7. PDF export

## Kurallar (zorunlu)
- **CRS:** Mesafe ve buffer hesapları **her zaman metrik CRS'de** yapılır. UTM zone koordinattan otomatik seçilir (boylamdan zone, enlemden kuzey/güney: EPSG:326xx / 327xx). EPSG:4326'da derece ile buffer/mesafe hesabı yapma.
- **Headless QGIS:** Container'da `QT_QPA_PLATFORM=offscreen` ayarlı olmalı.
- **OSM atıfı:** Her harita/PDF çıktısında "© OpenStreetMap contributors" görünür olmalı; kaynak notunda extract tarihi (Geofabrik).
- **Nominatim politikası:** En fazla 1 istek/sn, anlamlı bir User-Agent, sonuçları cache'le.
- **Türkiye sınırı:** Geocode sonucu `country_code != tr` ise ya da koordinat Türkiye dışındaysa net hata ver. Extract kapsamında olmayan yerde boş sonucu "veri yok" olarak raporla, "0 POI" gibi gösterme.
- **Büyük dosya:** `turkey.pbf` commit'lenmez, Docker imajına gömülmez.
- **LLM özeti:** Yalnızca `summary.json` / analiz tablosundaki verilere dayanır. Tabloda olmayan bilgi uydurulmaz, abartılı pazarlama dili kullanılmaz.
- **Portföy dili:** Kod, yorumlar, README ve demo çıktıları **İngilizce**, Upwork müşterisi / GIS işverenine yönelik.

## Klasör yapısı (hedef)
```
qgis-property-report/
├── CLAUDE.md
├── README.md              # EN, portföy yüzü (Coverage: Türkiye only)
├── docker-compose.yml
├── .gitignore             # data/, osm_test/
├── data/                  # turkey-latest.osm.pbf (repoya girmez)
├── scripts/
│   └── download_extract.ps1
├── qgis/
│   ├── Dockerfile
│   ├── generate_report.py # PyQGIS ana script
│   ├── report/poi_provider.py  # pbf'ten POI okuma → POI GeoJSON
│   ├── styles/            # .qml katman stilleri
│   └── templates/         # .qpt print layout şablonu
├── n8n/
│   └── workflow.json
├── samples/
│   ├── input.csv
│   ├── poi/               # 3 örnek lokasyonun hazır POI GeoJSON'ları
│   └── output/            # örnek PDF'ler
└── docs/                  # proje belgeleri (TR) + architecture.png
```

## Çalışma şekli
- **Dil:** Türkçe yazılırsa Türkçe, İngilizce yazılırsa İngilizce cevap ver; İngilizce mesajlardaki dilbilgisi hatalarını cevaba doğal şekilde ekle.
- **Önce çalışan minimum sürüm**, sonra iyileştirme. Net, kısa, uygulanabilir.
- **Kod/mantık hataları (Python/JS):** doğrudan çözüm ver.
- **Altyapı/mimari hataları (Docker, ağ, OAuth, webhook, deployment):** önce ipucu ver, kullanıcı denesin, sonra çözümü ver.
- **Entegrasyon sorun giderme:** katman katman izolasyon — bağlantı → kimlik doğrulama → istek/yanıt → veri formatı → hedef sistem. Bir katmanı doğrulamadan sonrakine geçme.
- **Dürüstlük:** Zayıf yaklaşımı, beklentiyi karşılamayan sonucu veya doğrulanmamış iddiayı açıkça söyle. Zaman kaybettiren belirsizlikte (kök neden bilinmiyorsa) bunu söyle, tahmini gerçek gibi sunma.
- Güncel faz ve görevler için `docs/03_ROADMAP.md`'ye bak.

**Ortam:** Windows 11 + PowerShell. Unix/macOS komutları değil PowerShell/cmd kullan (`Copy-Item`, `dir`, `Invoke-RestMethod`; `cp`, `ls`, `curl` değil; gerekirse `curl.exe`). Docker Desktop kullanılıyor, n8n Docker'da lokalde çalışıyor. Docker volume için `"${PWD}:/data"`.

- **Güncel faz: Faz 4 (n8n otomasyonu).** Faz 0–3 tamam (Faz 2'deki POI adımı Overpass ile yapılmıştı). 4.0 (POI kaynağı geçişi → yerel Geofabrik extract'ı) tamam (2026-11-14). 4a (Webhook → qgis container'da FastAPI `POST /generate` → PDF linki; n8n, `host.docker.internal:8000` üzerinden) tamam (2026-11-15). Sıradaki: 4b hata yönetimi; 4c Drive/Gmail teslimat; 4d Google Sheet tetikleyici + durum yazma. Detay: `docs/03_ROADMAP.md`.

- **Sistem Konfigürasyonu:** Windows 11 Pro 25H2 (build 26200.8039), Intel Core i7-13700H (2.40 GHz), 64 GB RAM, x64. Shell: PowerShell / cmd (Windows komutları kullan; Unix/macOS komutları değil).
