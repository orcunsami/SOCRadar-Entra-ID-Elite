# Ürün politikası

Kodun cevaplayamadığı sorular. Bunlar koda bakarak bilinemez; yanlış tahmin
edilince de sessizce yanlış bir ürün çıkar.

**Kural**: bir politika sorusuna cevap verilince buraya yazılır. Kod veya
doküman bu dosyayla çelişiyorsa **bu dosya kazanır**.

**Açık soru yok.** Yeni bir soru çıkarsa buraya "cevap bekleyen" başlığıyla
eklenir.

---

## 1. Sürümleme

**Karar:** Dışarıya çıkan release **her zaman `v1.0.0`**.

Kod ilerledikçe yeni tag kesilmez; `v1.0.0` release'inin varlığı güncellenir:

```bash
python3 scripts/build_package.py --out dist/FunctionApp.zip --deps-from <onceki.zip>
gh release upload v1.0.0 dist/FunctionApp.zip --clobber
```

`PackageUri` hiç değişmez. `1.1`, `1.2` gibi numaralar **iç versiyonlamadır**,
git geçmişinde kalır, müşteriye çıkmaz. Numara ancak Microsoft Partner veya
Content Hub fork'unda büyür (Content Hub V3 paketi ayrı kural: `3.0.0`).

**Yan etki, atlanmamalı:** `v1.0.0` varlığını değiştirmek, o URL'e bağlı
**çalışan** kurulumları bir sonraki yeniden başlatmada yeni koda geçirir.
Yapmadan önce hangi app'lerin bağlı olduğuna bak ve söyle:

```bash
az functionapp config appsettings list -g <rg> -n <app> \
  --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value" -o tsv
```

---

## 2. Sızan kimlik bilgisinin kaydı

**Karar:** Kimlik bilgisi **SOCRadar nasıl gönderdiyse
öyle** kaydedilir. Maskeli mi düz mü kararı SOCRadar platformunda, şirket
bazında verilir. Uygulama tarafında ikinci bir anahtar **yoktur**.

Gerekçe: iki taraflı anahtar, kaydın geldiği kaynakla çelişmesine yol açıyordu.
Müşteri SOCRadar'da düz istemişse ve bizim ayar kapalıysa, LAW'da şifre hiç
görünmüyordu ve kimse hangisine inanacağını bilmiyordu.

`password_masked` kolonu her hâlükârda yerelde üretilir, böylece bir pano
kimlik bilgisine hiç bakmadan güvenli bir şey gösterebilir.

---

## 3. İlk koşu ve mutasyon

**Karar:** `RunOnStartup` **`true` kalır**. Kurulum biter
bitmez former sync koşar ve `FormerApplyChanges=true` olduğu için yazar.

Gerekçe: "kur ve çalışsın" deneyimi, ilk koşuyu plan-only yapmanın getireceği
korumadan değerli. Koruma zaten katmanlı: bootstrap koşusunda silme sayısı
sıfır, `FormerMaxRemovals` ve `FormerMaxRemovalPercent` tavanları,
veri-bütünlüğü kapısı. Preview'e bakmak isteyen formda Apply changes'i
kapatarak deploy eder.

---

## 4. Grup tenant'ları

**Karar:** Şirket satırı olmayan grup tenant'ları için
forma bir alan eklendi (`Other group tenants`, `GROUP_TENANT_IDS`).

Netleştirme, çünkü ilk tarif yanlıştı: çapraz-tenant bastırma portal
kurulumuyla **zaten çalışıyordu**. `derive_group_tenants()` grid satırlarından
tam mesh kurar, yani her şirketin grup tenant'ları diğer satırların kendi
tenant'larıdır. Elle ayar gerekmiyordu.

Gerçek boşluk dardı: grid her satırda company ID **zorunlu** tuttuğu için,
gruba ait olup platformda şirketi olmayan bir tenant (holding tenant'ı gibi)
grid'de ifade edilemiyordu. Yeni alan tam olarak bunu karşılar: aktif üyeleri
her şirketin former listesine yazılır, kendisi liste almaz.

Uyarı formda yazılı: listelenen her tenant'ta uygulamanın consent'i olmalı,
yoksa anlık görüntü eksik sayılır ve silmeler tutulur.

---

## 5. Leak remediation

**Karar:** Kapı **kalktı**. Özellik formda varsayılan
kapalı gelir, açmak isteyen açar, sorumluluk açanda.

Varsayılanlar bunu güvenli kılıyor ve teste bağlandı
(`tests/test_form_contract.py`): `EnableLeakMonitoring=false`,
`LeakResponse=logOnly`, ve `respond` seçilmedikçe Graph yazma izinleri **hiç
istenmiyor**.

Kapıyla birlikte eklenen şart: üç aksiyonun geri alınamaz olduğu **formda**
yazılı. Daha önce sadece README'de yazıyordu; müşteri riski formda kabul
ediyor, uyarının orada olması gerekiyordu.

---

## 6. Defter büyümesi

**Karar:** Eski satırları silen bir temizlik koşusu
eklendi.

Her apply koşusunun sonunda, saklama süresini geçmiş satırlar sınırlı bir
dilim hâlinde silinir. Varsayılan 90 gün (`LEAK_LEDGER_RETENTION_DAYS`).

Tehlike tek yönlü: bir re-read'in hâlâ ulaşabildiği satırı silmek aynı kişiye
ikinci kez aksiyon uygulanmasına yol açar. Bunu **iki** koruma birlikte
engelliyor, biri yetmiyor:

**Taban.** `MIN_RETENTION_DAYS = 30`; ayar yanlış girilse bile uygulanır.
Süregelen pencere en fazla 24 saat, tutulan pencere birkaç kez denenir, 30 gün
ikisinin de üstünde.

**Aktif pencere clamp'i.** Taban tek başına yetmiyor ve ilk yazdığım gerekçe
yorumu bu yüzden yanlıştı. `LeakInitialLookbackDays` formda açık ve 365'e kadar
seçilebiliyor. 365 seçen bir müşterinin ilk koşusu satırlarını bir yıl geriye
damgalar; 90 günlük cutoff **o koşunun kendi defter kayıtlarını** siler ve
kurulumun göreceği en büyük batch'te idempotency kırılır. Bu yüzden cutoff
işlenen pencereye asla ulaşmıyor: `min(cutoff, active_window)`.

Gerçek Azure Table'da doğrulandı, clamp'siz hâli de yeniden üretilerek: cutoff
clamp'lenmediğinde aktif pencerenin kaydı **gerçekten** siliniyor.

**UTC.** Cutoff host saatinden değil UTC'den hesaplanıyor. Karşılaştırdığı
pencere damgaları da UTC'den üretiliyor (`sources/base_fetcher`,
`utils/checkpoint`), yerel saatle hesaplanan bir cutoff filtrelediği
değerlerden bir gün kayardı. Kod tabanında saat okuyan her çağrının
timezone-aware olması `tests/test_utc_convention.py` ile kilitli (AST taraması,
literal grep varyantları kaçırıyor).

Temizlik başarısız olursa koşu **düşmez**, satırlar kalır ve durum loglanır.
Bayat satır tutmak güvenli, koşuyu kaybetmek değil.

`leak/probe` kendi bölümüne (`probe:<company>`) yazar ve zamanlayıcı koşusu
oraya dokunmaz. Bu yüzden probe da işini bitirince kendi bölümünü temizler,
ama küçük dilimle (50 satır): çağıran cevabı bekliyor.

Ayar şablonda yazılı (`LeakLedgerRetentionDays` → `LEAK_LEDGER_RETENTION_DAYS`),
formda değil. Kodun okuyup şablonun yazmadığı bir isim sessizce varsayılana
düşer; ileri düzey ayar bile olsa şablonda görünür olmalı.

---

## 7. Sürüm görünürlüğü

**Karar:** Gerek yok. Kurulu app kendi sürümünü
söylemeyecek.

Hangi kodun koştuğu merak edilirse `WEBSITE_RUN_FROM_PACKAGE`'daki paket
indirilip bakılır. Kod tarafında sürüm sabiti tutulmaz, LAW'a yazılmaz, DCR
şemasına kolon eklenmez.

---

## 9. Denetlenebilirlik: objectId kayda girer

**Karar:** `entra_user_id` (Graph objectId) Log Analytics
satırına yazılır.

Gerekçe: müşterinin ilk sorusu "app bunu gerçekten yaptı mı" ve bunu bağımsız
kaynaktan, Microsoft Entra ID'nin **kendi** audit log'undan doğrulamak istiyor.
objectId iki kaydı birleştiren tek alan. O olmadan iki liste yan yana konamıyor.

Bu bir gizlilik tercihinin geri alınması **değildi**: alan `_clean_record`
içinde `_checkpoint_update` ve `_empty_marker` ile aynı "iç alan" kümesindeydi,
yani hiç düşünülmemişti.

Tuzak, yapılırken not edildi: kolon DCR stream'ine de eklenmezse kural onu
sessizce atar ve yükleme başarı bildirir. İkisi birlikte yapıldı, üç leak
stream'inde de canlı doğrulandı.

---

## 10. Feed'deki her adres Graph'a gitmez

**Karar:** Portal formuna opsiyonel "Your own email
domains" alanı eklendi (`VerifiedDomains` → `ENTRA_ID_VERIFIED_DOMAINS`).

Ayar kod tarafında zaten okunuyordu ama şablon hiç yazmıyordu, yani boştu ve
feed'in döndürdüğü **her adres** Microsoft Graph'a sorgulanıyordu, müşteriyle
ilgisi olsun olmasın.

Süzme lookup'tan **önce** çalışır: alan dışı adres app'in içinde düşer,
Microsoft'a hiç ulaşmaz. Boş bırakılırsa eski davranış aynen sürer, yani mevcut
kurulumlar etkilenmez.

---

## 11. İlk leak koşusu kurulumda gelir

**Karar:** `RUN_ON_STARTUP` former sync ile aynı parametreye
bağlandı.

Önce şablonda sabit `false` idi: leak monitoring açan müşteri ilk sonucu
zamanlamaya kadar, 6 saate kadar bekliyordu ve bunun beklenen davranış mı bozuk
kurulum mu olduğunu ayırt edemiyordu. Former sync ise kurulumda koşuyordu, yani
asimetri belgelenmemişti.

**Bilinen ve kabul edilen yan etki:** `respond` seçiliyse ilk koşu
kurulum anında gerçek hesap değişikliği yapabilir. Varsayılan `logOnly` ve form
geri alınamaz aksiyonları sayıyor, yani `respond`'u seçen bunu bilerek seçiyor.

---

## 12. Tek sorgu yüzeyi

**Karar:** Application Insights, şablonun oluşturduğu Log
Analytics workspace'ine bağlı kuruluyor.

Klasik bileşen olarak app'in izleri ayrı bir kaynakta duruyordu, audit tabloları
ise workspace'te. "App bunu yaptığını söylüyor, yaptı mı" sorusu iki ayrı yerden
sorgu ve birleştirme imkânı olmayan iki veri kümesi anlamına geliyordu.

Canlı doğrulandı: tek KQL sorgusu `AppTraces` ile `SOCRadar_EntraID_Audit_CL`'i
birlikte döndürüyor.

---

## 13. VIP kaydında adres: varsa kullanılır, yoksa uydurulmaz

**Karar.** Bir leak kaydının adresi, `@` taşıyan **ilk** alandan alınır
(`vipName` → `email` → `keyword`). Hiçbiri taşımıyorsa adres **yoktur**; kayıt
`entra_status = skipped_no_address` ile yazılır ve Microsoft Graph'a
sorulmaz.

**Neden.** VIP kayıtları çoğu zaman bir kişiyi **adıyla** anar. Canlı bir
örneklemde her kaydın `vipName`'i ad-soyaddı, hiçbirinde `@` yoktu ve hiçbiri
`email` alanı taşımıyordu. Eski kod `vipName`'i adres sayıyordu, yani Graph'a
"Ad Soyad" soruluyordu; Graph buna 404 veriyor — hiç duyulmamış bir adrese
verdiği cevabın aynısı. Sonuç: `error_count = 0` ile biten, tertemiz görünen ve
**yapısal olarak hiç kimseyi eşleştiremeyecek** bir koşu.

**Genişletilmeyecek yer.** `history` alanında adres bulunur (örneklemde
kayıtların yarısında), ama `operator` alt-alanındadır: kaydı işleyen analistin
adresi. Örneklemde hiçbiri kaydın kendi öznesiyle örtüşmedi. Oradan adres almak
lookup'ı ve silahlıysa aksiyonu **yanlış kişiye** yöneltir. Kod içine yazıldı.

---

## 14. Her kayıt bir kovaya düşer, toplam kapanır

**Karar.** `total_records = found_count + not_found_count + domain_filtered +
no_address_count`. Adresi olmadığı için bakılmayan kayıt kendi sayacına yazılır.

**Neden.** Önce bu kayıt hiçbir kovaya girmiyordu; toplam parçalardan büyüktü ve
farkın adı yoktu. Panoda "169 kayıt işlendi, 0 eşleşme" gören biri, kimsenin
eşleşmediğini mi yoksa kimseye bakılmadığını mı okuduğunu ayırt edemiyordu.

**Yan etki.** Kolon hem DCR stream'inde hem LAW tablosunda bildirildi (ikisi de
aynı `importAuditColumns` değişkeninden beslenir). **Mevcut kurulumlar** kendi
DCR'larını kurulum anında oluşturdu, yeni kolonu bilmezler, o yüzden alan orada
sessizce düşer — diğer sayaçları etkilenmez, yeniden deploy edince gelir.

---

## 15. Denetim satırı yalnız okunmuş olanı söyler

**Karar.** Kullanıcı araması Graph'tan `id,userPrincipalName,accountEnabled`
alanlarını **adıyla** ister. Gelmeyen alan için varsayılan **üretilmez**;
`entra_account_enabled` okunmadıysa boş kalır.

**Neden.** Graph, `GET /users/{id}` için sabit bir varsayılan alan kümesi
döndürür ve `accountEnabled` o kümede **yoktur**. Alan hiç gelmiyordu, kod ise
`get("accountEnabled", True)` ile dolduruyordu — yani eşleşen **her** kullanıcı
için "hesap açıktı" yazılıyordu, hiç okunmadan. Devre dışı bir hesap "açık"
görünürdü. "Bilinmiyor" doğru, "açık" tahmindi.

---

## 16. "Bakmadım" ile "bulamadım" ayrı raporlanır

**Karar.** `leak/probe` hiç tenant aramadıysa `apply_reason` bunu söyler ve
sebebini adlandırır (lookup kapalı / Graph token yok). "Bulunamadı" yalnız
gerçekten arandığında yazılır.

**Neden.** Operatörün adımı iki durumda farklı: gerçek ıska "adres dizinde yok"
demek, okunmamışlık "lookup'ı aç ya da token'ın neden verilmediğine bak" demek.
Boş sonucu olumsuz sonuç gibi raporlamak bu projenin tekrar eden hata sınıfı.

---

## 8. Yazılı karara bağlanmış diğer kısıtlar

| Konu | Karar |
|---|---|
| Test bitince Azure kaynağı kapatılır | Zorunlu |
| Dış repo'ya onaysız PR/push | Yasak |
| `create_incident` ve `ROPC` | Şablonda bilinçli kapalı (`main.bicep` yorumu) |
