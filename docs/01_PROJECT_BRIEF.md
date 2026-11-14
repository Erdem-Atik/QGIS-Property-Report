# Property Location Report Generator — Proje Özeti

## Amaç
1. Upwork'te STR / gayrimenkul nişi için gösterilebilir bir otomasyon portföy projesi.
2. GIS remote iş başvurularında haritacılık + kod + otomasyon becerisini kanıtlayan bir referans.

Sofistike olması gerekmiyor. Çalışan, temiz, iyi belgelenmiş ve görsel olarak güçlü olması yeterli.

## Tek cümlelik tanım
Türkiye'de bir adres (veya koordinat) girildiğinde çevresindeki önemli noktaları analiz edip, QGIS ile otomatik haritalı bir PDF "konum raporu" üreten ve AI ile kısa bir konum özeti yazan otomasyon.

## Kapsam kararı: yalnızca Türkiye (2026-11-14)
Proje yalnızca Türkiye'yi kapsar. Dünya çapı hedeflenmiyor. Bu, README'de açıkça yazılacak ("Coverage: Türkiye only"). Türkiye dışı girdi sessizce boş rapor üretmez, net bir hata mesajıyla reddedilir. Gerekçe: POI verisi yerel Geofabrik Türkiye extract'ından okunur (bkz. `02_ARCHITECTURE.md` → "POI veri kaynağı").

## Hedef müşteri (Upwork)
- STR (Airbnb/Booking) host'ları ve property manager'lar
- Emlak ajansları, yatırımcılar
- İlan açıklaması ve konum değerlendirmesi hazırlamak isteyen herkes

## Girdi → Çıktı
| Girdi | Çıktı |
|---|---|
| Google Sheet satırı veya webhook ile Türkiye'de bir adres / lat-lon | Harita PDF + GeoPackage + AI konum özeti |

## Akış
1. Tetikleyici (Google Sheet yeni satır / webhook) → n8n
2. Geocoding (Nominatim) → koordinat; sonuç Türkiye dışındaysa dur ve net hata ver
3. OSM verisi (yerel Geofabrik Türkiye extract'ı, `ogr2ogr` ile okunur): plaj, toplu taşıma, market, restoran, vb. Canlı bir POI servisi yok; internet bağlantısı gerekmez (geocoding ve LLM hariç)
4. Headless QGIS (PyQGIS): buffer (500 m / 1 km), mesafe hesabı, katman stili, print layout → PDF
5. LLM: mesafe tablosundan 3-4 cümlelik konum özeti
6. Çıktı: PDF Drive'a kaydedilir / e-postayla gönderilir, Sheet'e link yazılır

## Erişim modeli
- **Herkes (ücretsiz):** Geofabrik Türkiye extract'ı (~620 MB) bir kez indirilir (`data/` altına, repoya girmez). Ek ücret yok, üçüncü taraf POI servisine bağımlılık yok.
- **Repoyu klonlayıp indirme yapmak istemeyenler:** `samples/` altındaki hazır POI dosyaları ve örnek PDF'ler demoyu gösterir.
- Ücretli katman yok.

## Beceri vitrini
- Haritacılık: CRS yönetimi, buffer/mesafe analizi, katman stilleri, otomatik layout, OSM ham veri (PBF) işleme
- Kod: Python, PyQGIS, GDAL/ogr2ogr, API entegrasyonu
- Otomasyon: n8n, webhook, Sheets/Drive
- AI: LLM ile yapılandırılmış veriden metin üretimi

## Kapsam dışı (bilinçli olarak)
- Web arayüzü / dashboard
- Kullanıcı hesapları, ödeme
- Türkiye dışı adresler ve çok ülkeli veri
- Gerçek zamanlı / canlı POI verisi (extract tarihine bağlı; tarih rapora ve README'ye yazılır)
- Üçüncü taraf POI servisleri (Overpass, Google Places, Geoapify, Foursquare vb.)

## Başarı kriterleri
- [ ] Uçtan uca 3 örnek mülk için PDF üretildi (Türkiye içinde 3 farklı yer: kıyı kasabası, şehir merkezi, kırsal; hepsi kara üzerinde)
- [ ] Docker ile tek komutla çalışıyor (pbf volume olarak bağlı)
- [ ] Ek ücret ve harici POI servisi olmadan çalışıyor
- [ ] Türkiye dışı girdi net hata veriyor
- [ ] GitHub README (İngilizce) + mimari diyagram; kapsam (Türkiye only), veri kaynağı ve atıf açıkça yazılı
- [ ] 2 dakikalık Loom demosu
- [ ] Upwork portfolio kartı yayında
- [ ] GIS CV/LinkedIn'e proje eklendi

## Portföy başlığı (taslak, EN)
**Automated Location Intelligence Reports for Short-Term Rental Hosts (Türkiye)**
Turns an address in Türkiye into a branded map report with nearby amenities, distances and an AI-written location summary, using QGIS, Python, n8n and a local OpenStreetMap extract.
