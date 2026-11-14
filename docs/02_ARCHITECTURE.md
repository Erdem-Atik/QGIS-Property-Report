# Mimari ve Teknik Kararlar

## Bileşenler
```
[Google Sheet / Webhook]
        │
        ▼
     [ n8n ]  ── orkestrasyon
        │
        ├─► Nominatim (geocode)
        ├─► Python/PyQGIS container (POI okuma: yerel Geofabrik Türkiye extract'ı → analiz + harita PDF)
        ├─► LLM API (konum özeti)
        └─► Google Drive / Gmail (teslimat)
```

## Teknoloji seçimleri
| Katman | Seçim | Neden |
|---|---|---|
| GIS motoru | QGIS (PyQGIS, headless) | Hedef: QGIS becerisini göstermek |
| Konteyner | `qgis/qgis` Docker image | Kurulum derdi yok, tekrar üretilebilir; GDAL/`ogr2ogr` (OSM sürücüsüyle) imajda hazır |
| Orkestrasyon | n8n | Mevcut deneyim (rezervasyon projesi) |
| Geocoding | Nominatim | Ücretsiz, geniş community desteği (public servis, bu çalışmada test edilmedi; cache + 1 istek/sn şart) |
| POI verisi | Yerel Geofabrik Türkiye extract'ı (`turkey-latest.osm.pbf`) + `ogr2ogr -spat` | Public Overpass sunucuları güvenilmez çıktı; yerel dosya sunucudan bağımsız ve tekrarlanabilir (bkz. "POI veri kaynağı") |
| AI | Mevcut LLM API (Groq/OpenAI/Claude) | Zaten kullanılan servisler |
| Veri formatı | GeoPackage (.gpkg) | Açık standart, QGIS native |

Seçim ilkesi: büyük community desteği olan araçlar (YouTube, Stack Overflow, resmi doküman).

## POI veri kaynağı: yerel Geofabrik Türkiye extract'ı
**Karar tarihi: 2026-11-14.** Overpass'tan vazgeçildi. Kapsam yalnızca Türkiye (bkz. `01_PROJECT_BRIEF.md`).

### Neden Overpass bırakıldı (gözlemler)
- 2026-11-12: `overpass-api.de` → ConnectionError, `overpass.private.coffee` → ReadTimeout.
- 2026-11-13: `overpass-api.de/api/status` → 502 "at capacity".
- 2026-11-14 (ev ağı ve telefon hotspot'ı, ikisinde de aynı sonuç, yani sorun yerel ağ değil):
  - `overpass-api.de`: DNS ve TCP/443 başarılı, ama API 406 Not Acceptable döndü, anlamlı User-Agent eklenince de düzelmedi.
  - `overpass.private.coffee`, `overpass.kumi.systems`: 40 sn zaman aşımı.
  - `overpass.osm.jp`: DNS çözülemedi. `maps.mail.ru/.../overpass`: 504.
- Daha önce 3 lokasyonda çalışmıştı; neden artık çalışmadığı kök nedeniyle belirlenmedi. Daha fazla zaman harcanmadı.

### Çalışan yol (doğrulandı)
- Dosya: `https://download.geofabrik.de/europe/turkey-latest.osm.pbf` (~620 MB; 2026-11-14'te indirilen sürüm 13 Kasım 2026 tarihli). Repoya girmez: `data/` altında, `.gitignore`'da. Docker'da volume olarak bağlanır. Yol: `OSM_PBF_PATH`.
- Okuma: `ogr2ogr -f GeoJSON out.geojson turkey.pbf points -spat xmin ymin xmax ymax` (PowerShell'de `docker run --rm -v "${PWD}:/data" qgis/qgis ogr2ogr ...`). `-spat` sırası: lon_min lat_min lon_max lat_max.
- Doğrulama: Antalya merkezi (36.887 K, 30.704 D), ±0.0113° kutu → `points` katmanında 1852 nokta, 705'i `amenity` etiketli; süre ~7 sn (her sorgu tüm dosyayı tarar).
- Etiketler `other_tags` (hstore benzeri metin) içindedir; `"amenity"=>"..."` gibi parse edilmelidir.

### Bilinen sınırlar (README'ye yazılacak)
- **Yalnızca `points` katmanı yetmez.** Plaj, park, okul, birçok mağaza OSM'de poligondur. `multipolygons` katmanı da okunur; poligonlar poligon olarak kalır (centroid'e indirgenmez), `analysis.py` mesafeyi en yakın kenara ölçer (uzun plajlarda centroid yanıltıcı olur). Yürüme yolları gerekirse `lines`. Yukarıdaki 1852/705 sayıları alt sınırdır.
- **Veri güncelliği:** Extract tarihine bağlıdır (Geofabrik günlük günceller, biz elle indiririz). Rapor/README'de extract tarihi belirtilir.
- **Performans:** points + multipolygons tek geçişte 8–10 sn/sorgu (ölçüldü 2026-11-14). İlk sürümde kabul. Gerekirse optimizasyon: demo bölgeleri için bir kez küçük bir `.gpkg`'ye kes.
- **Kapsam:** Yalnızca Türkiye. Extract dışındaki bir koordinatta sonuç boş çıkar; bu "veri yok" olarak raporlanmalı, "0 POI" gibi gösterilmemeli.
- **Veri kalitesi:** Sonuçlar OSM etiket kalitesine bağlıdır.
- **Lisans/atıf:** ODbL; haritada ve README'de "© OpenStreetMap contributors", kaynak notunda "Geofabrik extract, <tarih>".

### Yapılandırma
`OSM_PBF_PATH` (pbf dosyası yolu, container içinde; compose varsayılanı `/data/turkey-latest.osm.pbf`). Eski `OVERPASS_*` değişkenleri kaldırıldı.

Uygulama: `qgis/report/poi_provider.py` (GDAL OSM sürücüsü, Python API; `ogr2ogr -spat` ile aynı motor). Extract tarihi PBF header'ındaki `osmosis_replication_timestamp`'ten okunur; yoksa `scripts/download_extract.ps1`'in yazdığı `<pbf>.timestamp`. Tarih bilinmiyorsa tahmin edilmez.

### Değerlendirilip seçilmeyenler
- **Overpass (public, ücretli, self-host):** yukarıdaki nedenlerle bırakıldı; ücretli sağlayıcı da seçilmedi.
- **Overture Maps (DuckDB/GeoParquet):** uzaktan bbox sorgusu mümkün, ama farklı şema ve kategori sistemi (adapter gerekir), lisans/atıf doğrulanmadı, Türkiye kapsam kalitesi doğrulanmadı. İleride düşünülebilir.
- **osm2pgsql + PostGIS:** bu kapsam için fazla ağır (veritabanı, import, bakım).
- **QLever OSM:** üçüncü taraf sunucu, SLA yok, SPARQL; public Overpass ile aynı güvenilirlik riski.
- Geoapify / Foursquare / Google Places: farklı şema, ücret/kota/kullanım kısıtları; kapsam büyür.

## Klasör yapısı (hedef)
```
qgis-property-report/
├── CLAUDE.md
├── README.md              # EN, portföy yüzü (Coverage: Türkiye only)
├── docker-compose.yml
├── .gitignore             # data/, osm_test/ burada
├── data/                  # turkey-latest.osm.pbf (repoya girmez)
├── scripts/
│   └── download_extract.ps1   # Geofabrik pbf indirme
├── qgis/
│   ├── Dockerfile
│   ├── generate_report.py # PyQGIS ana script
│   ├── report/poi_provider.py  # pbf'ten POI okuma (GDAL OSM, -spat) → POI GeoJSON
│   ├── styles/            # .qml katman stilleri
│   └── templates/         # .qpt print layout şablonu
├── n8n/
│   └── workflow.json
├── samples/
│   ├── input.csv
│   ├── poi/               # 3 örnek lokasyonun hazır POI GeoJSON'ları (pbf olmadan demo)
│   └── output/            # örnek PDF'ler
└── docs/                  # proje belgeleri (TR) + architecture.png
```

## PyQGIS script arayüzü
```
python generate_report.py --lat 36.887 --lon 30.704 --name "Villa Demo" --out /out
```
Girdi: koordinat + isim + (opsiyonel) POI JSON. POI JSON verilmezse `poi_provider` pbf'ten okur.
Çıktı: `report.pdf`, `analysis.gpkg`, `summary.json` (mesafe tablosu).
Not: Eski örnek koordinat 36.8, 30.7 Antalya merkezinin ~10 km güneyinde, büyük olasılıkla açık denizde; 1 km kutuda 0 nokta çıktı. Kullanılmayacak.

## Adres çözümleme (geocoding) kuralları
Nominatim adresi *parse etmez*, *geocode eder*; `addressdetails=1` ile sonucu bileşenlerine ayırıp döndürür. Kapsam Türkiye olduğu için doğruluk seviyesi raporda şeffaf gösterilir.

1. **Aday sonuçlar:** `limit=3` ile sorgula. Sonuç boşsa workflow net bir hata mesajıyla durur (sessizce devam etme).
2. **Ülke kontrolü:** Sonucun ülkesi Türkiye değilse (`country_code != tr`) net hata ver. lat/lon girişinde kaba bir sınır kontrolü uygula (yaklaşık lon 25.6–44.9, lat 35.8–42.2; komşu ülke ve ada kenarlarında kaba kalır, bu yüzden boş POI sonucu ayrıca ele alınır).
3. **Doğruluk seviyesi:** İlk sonucun `class`/`type` değerine göre sınıflandır:
   - `house`, `building`, `address` → **kesin** (bina seviyesi)
   - `road`, `residential` (sokak) → **yaklaşık (sokak seviyesi)**
   - `suburb`, `neighbourhood`, `city`, `town` → **düşük (mahalle/şehir merkezi)**
4. **Rapor notu:** Seviye "kesin" değilse PDF'e ve `summary.json`'a "Location accuracy: approximate (street level)" notu eklenir. LLM özeti de bu bilgiyi aktarır.
5. **Fallback zinciri** (bir adım sonuç vermezse sonrakine geç):
   1. Serbest metin sorgu (`q=`)
   2. Sadeleştirilmiş adres (daire/kat/blok bilgisini çıkar, "Mah./Sok./No:" kısaltmalarını normalize et)
   3. Yapılandırılmış sorgu (`street`, `city`, `country`)
   4. Hâlâ yoksa: kullanıcıdan lat/lon iste
6. **lat/lon girişi her zaman desteklenir:** Adres çözülemezse veya doğruluk düşükse (ör. Airbnb/Maps pininden alınan) koordinat doğrudan kullanılır, geocoding atlanır.
7. **Türkiye notu:** OSM'de bina numarası verisi eksik olduğundan Türkçe adresler çoğunlukla sokak/mahalle seviyesine düşer.
8. **Politika:** 1 istek/sn, anlamlı User-Agent, sonuçları cache'le (aynı adres tekrar sorgulanmaz).

## Analiz adımları (QGIS içinde)
1. Mülk noktasını EPSG:4326 → yerel UTM'e (metrik) dönüştür
2. 500 m ve 1000 m buffer üret
3. POI katmanlarını buffer ile kes, kategoriye göre say
4. En yakın POI mesafesini hesapla (kategori başına)
5. Katmanları stille, basemap ekle (OSM XYZ tile)
6. Print layout şablonuna yerleştir: harita, başlık, mesafe tablosu, AI özeti, kaynak notu
7. PDF export

## Önemli dikkat noktaları
- **CRS:** Mesafe/buffer hesabı mutlaka metrik CRS'de (UTM zone koordinattan otomatik seçilecek).
- **OSM atıfı:** Haritada "© OpenStreetMap contributors" zorunlu; kaynak notuna extract tarihi.
- **Nominatim kullanım politikası:** Saniyede 1 istek, anlamlı User-Agent, cache.
- **POI okuma:** `-spat` sırası lon/lat (x/y). Kutuyu küçük tut. `points` + `multipolygons` birlikte okunmalı.
- **Headless QGIS:** `QT_QPA_PLATFORM=offscreen` gerekir.
- **Büyük dosya:** `turkey.pbf` hiçbir zaman commit'lenmez; Docker imajına gömülmez, volume ile bağlanır.

## Hata ayıklama yaklaşımı (katman katman izolasyon)
Bağlantı → kimlik doğrulama → istek/yanıt → veri formatı → hedef sistem.
Her entegrasyon sorununda bu sırayla ilerle, bir katmanı doğrulamadan sonrakine geçme.
Örnek (pbf okuma): dosya var/indi mi → araç çalışıyor mu (`ogr2ogr` hata kodu) → filtresiz okuma (`-limit 5`) → filtreli okuma → sonuç beklenen yerde mi (koordinat kara üzerinde mi).
