# Statik analiz: ne kapı, ne değil

**Ölçüm tarihi: 29 Temmuz 2026.** Yöntem: ruff ve mypy bu repo üzerinde
koşturuldu, sonra ikisi de **bu projede bulunmuş kusurlara** karşı
sınandı. Sınama kanıt zorunluydu: kusur repo kopyasında geri alındı, araç
koşturuldu, bulgu çıkıyor mu bakıldı.

Neden bu soru: testler yeşilken gerçek kusurlar durabiliyordu ve testle değil kod
okunarak bulunuyordu. Yani "bulgu üretiyor"
bir aracı kapı yapmak için gerekçe değil. Soru şu: **okumanın bulduğunu
yakalıyor mu?**

## Sonuç tablosu

| Araç | Bulgu | kusurlardan yakaladığı | Karar |
|---|---|---|---|
| ruff 0.16 | 164 (varsayılan) · 2 753 (`--select ALL`) · 2 984 (`ALL --preview`) | **0** | CI kapısı DEĞİL |
| mypy 2.3 | 34 (varsayılan) · 255 (`--strict`) | **0** | CI kapısı DEĞİL |
| coverage | üretim kodu %59 | — | **Taban olarak CI'da** |

`--select ALL --preview` her seçimin üst kümesi olduğu için ruff kararı
yapılandırma seçimine bağlı değil. mypy tarafında her mutasyon ayrı dizinde,
`--no-incremental --cache-dir /dev/null` ile, `(dosya, mesaj)` çoklu kümesi
karşılaştırılarak sınandı; satır kaymaları ne bulgu uydurabilir ne maskeleyebilir.

Neden: kusurların çoğu **eksik mantıktı** — var olmayan bir satır, yanlış
sıralanmış iki kapı, bildirilmemiş bir şema kolonu. Statik analiz var olan kodu
inceler; olmayan kodu inceleyemez. Bir kusur (ARM çıktısının yalanı) bicep ve
shell içinde, Python aracının görüş alanında değil.

## Yine de değer üretti: tek seferlik koşum 3 gerçek bulgu verdi

Kapı olmaması "işe yaramaz" demek değil. Aynı koşum, o kusurların dışında üç
gerçek sorun gösterdi:

| Bulgu | Yer | Durum |
|---|---|---|
| `date.today()` naive, kod tabanının UTC konvansiyonunu kırıyor | `actions/action_ledger.py:53` | ✅ düzeltildi |
| `except Exception: pass` bir yaşam-döngüsü olayını yutuyor (kendi yorumu "alertable olmalı" diyor) | `function_app.py:1069` | ✅ düzeltildi |
| `except Exception: pass` başarısız şirketin audit satırını yutuyor | `function_app.py:1213` | ✅ düzeltildi |

Bu yüzden karar "kullanmayın" değil: **sürüm öncesi elle koş, kapı yapma.**

```bash
python3 -m venv /tmp/ruff && /tmp/ruff/bin/pip install -q ruff
/tmp/ruff/bin/ruff check FunctionApp --output-format=concise
```

Çıkan 164 bulgunun ~160'ı stil ya da yanlış pozitif. İşe yarayan kısım
`S110` (sessiz `except`) ve `DTZ` (naive tarih) aileleri; bu ikisi bu projenin
belgelenmiş kusur sınıfına doğrudan
denk geliyor. Gözle taranırken bunlara bak, gerisini geç.

## Kapanan kalem: naive tarih (ve yanında bulunan gerçek bug)

`cutoff_date` varsayılanda `date.today()` kullanıyordu; kod tabanındaki **tek**
naive tarih çağrısıydı. Düzeltildi: `datetime.now(timezone.utc).date()`.

Etkisi bugün latent olurdu (`WEBSITE_TIME_ZONE` hiçbir yerde ayarlı değil, Azure
Functions Linux worker varsayılanı UTC). Ama düzeltirken aynı fonksiyonda **şu
an ulaşılabilir** bir bug çıktı:

`MIN_RETENTION_DAYS = 30`'un gerekçesini "en büyük süregelen pencere 24 saat"
diye yazmıştım. Yanlış. `LeakInitialLookbackDays` formda açık ve 365'e kadar
seçilebiliyor. 365 seçen bir müşterinin ilk koşusu satırlarını bir yıl geriye
damgalar, 90 günlük cutoff onları siler ve **o koşunun kendi idempotency
kayıtları** yok olur. En büyük batch'te aynı kişiye ikinci kez aksiyon.

Düzeltme: cutoff işlenen pencereye asla ulaşmıyor, `min(cutoff, active_window)`.
Her iki çağrı yeri aktif pencereyi geçiriyor.

Gerçek Azure Table'da doğrulandı (11 kontrol), içinde kusuru yeniden üreten bir
adımla: clamp'siz cutoff aktif pencerenin kaydını **gerçekten** siliyor.

Konvansiyon artık kilitli: `tests/test_utc_convention.py` AST ile tüm ağacı
tarar (`FunctionApp/` + `scripts/`), `date.today()` · `datetime.date.today()` ·
aliaslı import · `utcnow` · `fromtimestamp` · `utcfromtimestamp` varyantlarını
ayrı ayrı yakalar. Literal grep bunların çoğunu kaçırırdı.

## Kapsam eşiği neden 63

`--omit='tests/*'` ile ölçülen üretim kodu değeri. Testler sayıldığında rakam
şişiyor, çünkü test dosyaları tanımı gereği neredeyse tamamen koşuyor — o
sayıyla bir taban koymak, test yazınca yükselen ama tek satır uygulama kodu
kapsamayan bir gösterge üretirdi.

Eşik **hedef değil taban**: geriye gitmesin diye var. Gerçekten aşılırsa
yükseltilir, dal geçsin diye asla indirilmez. İlk konulduğunda 59'du; Graph
mutasyon sonuçları teste bağlanınca üretim kapsamı 63'e çıktı ve taban da
yükseltildi. Kırıldığı doğrulandı: eşik değeri geçer, bir üstü exit 2 verir.

En düşük kapsanan yerler ve neden önemli oldukları:

| Modül | Kapsam | Orada bir kusur ne yapar |
|---|---|---|
| `actions/entra_id.py` | %23 → **%56** | Her gerçek Graph mutasyonu burada. Durum kodu kontrolü bir kayarsa 403 başarı sayılır, defter "yapıldı" yazar, kimse dokunulmamış hesabı bir daha denemez. `tests/test_graph_mutation_results.py` ile kapatıldı: 11 reddetme kodu × 7 sarmalayıcı, artı transport hatası, artı "zaten istenen durumda" istisnasının dar kalması. 4/4 mutasyon yakalandı |
| `actions/former_lock.py` | %38 | ETag devralma yarışı; kilit bozulursa elle ekleme apply-mode readback'in ortasına düşüp sahiplik muhasebesini bozar |
| `utils/checkpoint.py` | %40 | `save()` alan filtreliyor; bir alan sessizce düşerse sonraki koşu yanlış pencereden devam eder |
| `actions/former_ownership.py` | %53 | Hangi hesabın "bizim" olduğuna karar veren kapı; ters dönerse başkasının kaydı silinir |

Bu tablo eşiği yükseltmek için değil, **hangi testin yazılmaya değer olduğunu**
seçmek için. Yüzdeyi kovalamak bu repo'da işe yaramadı.
