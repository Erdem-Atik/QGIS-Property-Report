# Yol Haritası (1–2 hafta sonu)

## Faz 0 — Hazırlık (1–2 saat)
- [ ] GitHub repo aç: `qgis-property-report`
- [ ] Klasör yapısını oluştur
- [ ] Docker kurulu mu kontrol et, `qgis/qgis` image'ını çek
- [ ] Test için 3 lokasyon seç: Türkiye içinde, kara üzerinde (kıyı kasabası, şehir merkezi, kırsal). Eski 36.8, 30.7 noktası denizde, kullanma; Antalya merkezi için 36.887, 30.704 doğrulandı

## Faz 1 — GIS çekirdeği (en önemli faz) ✅
- [x] Container içinde PyQGIS import testi (`from qgis.core import *`)
- [x] Tek koordinattan buffer üret, GeoPackage'e yaz
- [x] Elle hazırlanmış küçük POI GeoJSON ile mesafe/sayım hesapla
- [x] OSM XYZ basemap ekle, katmanları stille
- [x] Print layout şablonu (.qpt) hazırla, PDF export al
- **Kontrol noktası:** Tek komutla koordinattan PDF çıkıyor.

## Faz 2 — Veri entegrasyonu ✅ (POI kaynağı Faz 4.0'da değişiyor)
- [x] Nominatim geocode fonksiyonu (cache + User-Agent)
- [x] POI sorgusu: kategori başına POI çek, GeoJSON'a çevir (Overpass ile yapıldı; Faz 4.0'da yerel extract'a geçiliyor)
- [x] POI verisini PyQGIS script'ine bağla
- [x] 3 test lokasyonu için PDF üret
- **Kontrol noktası:** Adres yazınca PDF çıkıyor.

## Faz 3 — AI katmanı ✅
- [x] `summary.json` (mesafe tablosu) → LLM prompt
- [x] 3–4 cümlelik, abartısız konum özeti (halüsinasyon yok: sadece tablodaki verilere dayan)
- [x] Özeti PDF layout'a ekle

## Faz 4 — n8n otomasyonu (şu an burada)
Ortam: n8n Docker'da lokalde çalışıyor. Sıra: 4.0 → 4a → 4b → 4c → 4d (önce çalışan minimum sürüm). Kodlama işleri Claude Code CLI'dan yapılır.

### 4.0 — POI kaynağı geçişi: Overpass → yerel Geofabrik Türkiye extract'ı (karar: 2026-11-14)
Bağlam: public Overpass sunucuları 12–14 Kasım'da güvenilmez çıktı (ev ağı ve hotspot'ta aynı sonuç). Kapsam yalnızca Türkiye. Ayrıntı: `02_ARCHITECTURE.md` → "POI veri kaynağı".
- [x] Geofabrik yolu elle doğrulandı (indirme 623 MB, `ogr2ogr -spat`, Antalya merkezi: 1852 nokta / 705 amenity, ~7 sn)
- [x] `osm_test/turkey.pbf` → `data/turkey-latest.osm.pbf`; `data/`, `osm_test/`, `*.osm.pbf` `.gitignore`'da (pbf commit'lenmez)
- [x] `scripts/download_extract.ps1`: pbf indirme + MD5 kontrolü + `Last-Modified` → `.timestamp` (yedek tarih kaynağı)
- [x] `qgis/report/poi_provider.py`: (lat, lon, radius) → `generate_report.py`'nin beklediği POI GeoJSON. GDAL OSM sürücüsü, `points` + `multipolygons` (tek geçiş, `SET interest_layers`), `other_tags` parse, metrik (UTM) yarıçap filtresi. Poligonlar poligon kalır (centroid değil): `analysis.py` kenara mesafe ölçer. Extract tarihi PBF header'ından (`osmosis_replication_timestamp`). Kategoriler mevcut 6 (beach, supermarket, restaurant, cafe, pharmacy, bus_stop)
- [x] Overpass kodu, cache'i, `import_overpass.py`, `samples/overpass_cache/`, `--print-query` ve `OVERPASS_*` kaldırıldı; `OSM_PBF_PATH` eklendi (compose: `./data:/data:ro`)
- [x] Türkiye dışı girdi: net hata (geocode `country_code` + kaba bbox, exit 2); çevrede hiç OSM verisi yoksa (extract dışı / deniz) `NoOsmDataError`, "0 POI" gösterilmez
- [x] 3 örnek lokasyon (Kaş, Kaleiçi, Elmalı): `samples/poi/*.geojson` (metadata + extract tarihi ile), `samples/output/*.pdf`
- [x] Birim test (`tests/fixtures/mini.osm`, mock'suz) + canlı test (Antalya 36.887, 30.704; deniz noktası)
- Ölçüm (2026-11-14, extract OSM verisi 2026-11-12): POI okuma 8–10 sn/rapor (Antalya merkezi: 524 POI, 1500 m), raporun tamamı ~14 sn (`--no-summary`)
- [ ] Takip: yeni kategoriler (park, istasyon, okul vb.): tablo/layout etkisi ayrıca değerlendirilecek
- **Kontrol noktası:** İnternetsiz POI çekimiyle (geocode hariç) koordinattan PDF çıkıyor. ✅ (`--network none` ile doğrulandı)

### 4a–4d
- [ ] 4a: Container'a HTTP katmanı (`qgis/server.py`, FastAPI, `POST /generate`), `Invoke-RestMethod` ile elle test
- [ ] 4a: `docker-compose.yml`: n8n ve qgis servisleri aynı network'te; qgis servisine `data/` volume'u bağlı (`OSM_PBF_PATH`)
- [ ] 4a: Workflow: Webhook → HTTP Request (`/generate`) → Respond to Webhook
- [ ] 4b: Hata yönetimi: geocode bulunamadı, Türkiye dışı girdi, pbf dosyası yok/okunamıyor
- [ ] 4c: Teslimat: Drive'a yükle / Gmail ile gönder
- [ ] 4d: Tetikleyici: Google Sheet yeni satır; Sheet'e durum + PDF linki yaz

## Faz 5 — Portföy paketi
- [ ] README (EN): problem, çözüm, mimari diyagram, ekran görüntüleri, kurulum
- [ ] README (EN): **kapsam: Coverage is Türkiye only** (Türkiye dışı girdi reddedilir)
- [ ] README (EN): veri kaynağı: Geofabrik Türkiye extract'ı, nasıl indirilir, extract tarihi, ODbL atıfı ("© OpenStreetMap contributors"), sonuçlar OSM etiket kalitesine bağlı, ~7 sn/sorgu
- [x] `samples/poi/` içinde 3 örnek lokasyonun POI dosyaları (pbf indirmeden demo görülebilsin)
- [x] `samples/output/` içine 3 örnek PDF (Faz 4.0'da üretildi)
- [ ] Loom demosu (2 dk): Sheet'e adres yaz → PDF gelsin
- [ ] Upwork portfolio kartı (EN başlık + 3 maddelik açıklama)
- [ ] LinkedIn / GIS CV'ye ekle: "PyQGIS automation, OSM data pipeline, n8n"

## Opsiyonel (zaman kalırsa)
- [ ] Hız: demo bölgeleri için pbf'i bir kez küçük `.gpkg`'ye kes (şu an her sorgu 8–10 sn)
- [ ] Yürüme süresi için routing (OpenRouteService / pgRouting yerine basit API)
- [ ] İkinci şablon: yatırımcı için daha detaylı rapor
- [ ] Birim testleri (geocode, CRS seçimi)
- [ ] Overture Maps (DuckDB) adapter'ı: yalnızca extract yaklaşımı yetmezse (farklı şema, lisans ve Türkiye kalitesi önce doğrulanmalı)

## Sonraki proje fikri (backup)
UAV/İHA verisinden alan-hacim raporu otomasyonu — GIS remote iş için daha güçlü ikinci portföy parçası.
