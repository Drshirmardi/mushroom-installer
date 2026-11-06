# نصب و مدیریت سرور Mushroom

نصاب مستقل برای **Ubuntu Server 24.04 LTS روی x86_64/amd64**، محیط توسعهٔ مشترک و production واقعی است. به clone ریپوی خصوصی، Node روی host یا توکن دائمی GitHub نیاز ندارد. نصب، سرور را آماده می‌کند؛ اولین اجرای پروژه از GitHub Actions انجام می‌شود. ساخت بسته را بعد از merge شدن همین تغییرات انجام دهید تا نصاب و workflowهای مخزن قرارداد یکسان داشته باشند. ARM در این نسخه پشتیبانی نمی‌شود، چون pipeline فعلی ایمیج‌ها را روی runner استاندارد amd64 می‌سازد.

## دریافت و به‌روزرسانی عمومی

روش اصلی نصب یا جایگزینی ابزار قدیمی، فرمان کوتاه [صفحهٔ اصلی](../README.md) است. این مسیر فایل‌های نصاب را به نسخهٔ مشخص منتقل می‌کند و روی سرور نصب‌شده پنل را باز می‌کند. برای دریافت ابزار بدون اجرای پنل یا راه‌اندازی از `--no-run` استفاده کنید.

فرمان‌های `sudo mushroom --version`، `sudo mushroom update --check`، `sudo mushroom update` و `sudo mushroom update --version v1.0.12` نسخهٔ ابزار را مدیریت می‌کنند. برگشت به نسخهٔ قدیمی‌تر تأیید صریح می‌خواهد؛ برای اجرای غیرتعاملیِ همین انتخاب، `--yes` را اضافه کنید. قطع اینترنت مانع کار پنل و عملیات معمول نیست. آپدیت ابزار باعث استقرار مجدد برنامه یا تغییر سکرت‌های محیط نمی‌شود.

## ساخت و انتقال دستی بسته

در ریشهٔ همین repository روی سیستم خودتان:

```console
python installation/build.py
```

دو فایل در `installation/dist/` تولید می‌شود: `mushroom-installer.tar.gz` و checksum آن با پسوند `.sha256`. هر دو را با روش امن به سرور منتقل کنید. آرشیو با فهرست مجاز صریح ساخته می‌شود؛ `.env`ها، سورس خصوصی برنامه‌ها، کلیدها و فایل‌های خروجی داخل آن نیستند. checksum را از همین کانال معتبر دریافت کنید.

روی سرور:

```bash
sha256sum --check mushroom-installer.tar.gz.sha256
tar -xzf mushroom-installer.tar.gz
cd mushroom-installer
sudo bash installation/install.sh
```

در Termius و سایر کلاینت‌های SSH از encoding برابر **UTF-8** استفاده کنید. نسخهٔ فعلی ورودی ناخوانا را
بدون نمایش مقدارش رد می‌کند و همان سؤال را دوباره می‌پرسد؛ رمزها نیز همین کنترل را دارند و بایت خراب
به کاراکتر جایگزین تبدیل نمی‌شود. مقدار را دوباره تایپ کنید. در صورت قطع ورودی، اتصال SSH را باز کرده
و نصب را دوباره اجرا کنید. خطاهای غیرمنتظره فقط نوع، شماره خط و در خطاهای سیستم شمارهٔ errno را گزارش
می‌کنند؛ برای عیب‌یابی همان کد را ارسال کنید، نه سکرت یا فایل تنظیمات کامل.

برای جایگزینی بستهٔ قدیمی روی سرور، دو فایل تازهٔ آرشیو/checksum را در `/root` بارگذاری کنید و اجرا کنید:

```bash
cd /root
sha256sum --check mushroom-installer.tar.gz.sha256 &&
tar -xzf mushroom-installer.tar.gz &&
bash /root/mushroom-installer/installation/install.sh
```

فایل‌های وضعیت و سکرت‌های موجود بیرون از پوشهٔ آرشیو هستند؛ برای رفع خطای ورودی آن‌ها را حذف نکنید.

اگر نسخهٔ قبلی بعد از نصب Docker در `allocate_networks` با `NoneType` متوقف شد، علت تنظیمات خالی
شبکه‌های پیش‌فرض `host` و `none` است. نسخهٔ فعلی این حالت را پوشش می‌دهد. برای همین اصلاح می‌توانید
فقط فایل تازهٔ `installation/manager.py` را روی فایل هم‌نام داخل بستهٔ سرور جایگزین و همان launcher را
دوباره اجرا کنید. مرحلهٔ پکیج‌ها در وضعیت ذخیره شده و تکرار نمی‌شود؛ شبکه‌ها یا سکرت‌ها را حذف نکنید.

پروفایل production یک staging مستقل هم آماده می‌کند. برای production از PostgreSQL خارجی نسخهٔ ۱۶ یا بالاتر با TLS استفاده می‌شود؛ ابزار دیتابیس production را روی VPS نصب نمی‌کند. برای dev/staging دیتابیس Docker اختصاصی ساخته می‌شود. زیرساخت‌ها و دسترسی‌های production جدا هستند؛ روی یک host، اعضای گروه Docker همچنان اختیار گستردهٔ همان host دارند. برای جداسازی امنیتی کامل، سرورهای مستقل انتخاب کنید.

## سؤال‌ها و خروجی‌ها

نصاب محیط، دامنه‌ها، IP عمومی IPv4، پورت SSH فعلی، مخزن خصوصی، نام کاربری GitHub تأییدکننده، ایمیل گواهی، حالت پیامک/پرداخت، بکاپ و نسخهٔ PostgreSQL را می‌پرسد. سپس برای هر محیط مشخصات کلینیک و مدیر اولیه را می‌گیرد. production علاوه بر این به URL مخفی PostgreSQL و مسیر سند رضایت تأییدشده نیاز دارد. رمز مدیر حداقل ۱۵ و حداکثر ۱۲۸ نویسه است و در اولین استقرار با سیاست واقعی برنامه کنترل می‌شود.

پیش‌فرض دامنه‌ها برای `example.com`:

| محیط | وب | اپ | داشبورد | API |
|---|---|---|---|---|
| production | example.com | app.example.com | dashboard.example.com | api.example.com |
| staging | staging.example.com | staging-app.example.com | staging-dashboard.example.com | staging-api.example.com |
| dev | dev.example.com | dev-app.example.com | dev-dashboard.example.com | dev-api.example.com |

همه قابل تغییر هستند. این نسخه مستقیماً با A record و IPv4 کار می‌کند: رکوردها باید DNS-only باشند و AAAA ناسازگار نداشته باشند. کنترل‌پنل DNS بیرون از سرور است؛ نصاب رکوردهای لازم را اعلام می‌کند، صحت آن‌ها را بررسی می‌کند و تا آماده‌شدنشان موفقیت اعلام نمی‌کند. بعد از تنظیم DNS دوباره همان فرمان نصب را اجرا کنید. Caddy صدور/تمدید گواهی و WebSocket را مدیریت می‌کند. برای CDN، DNS API، wildcard و DNS challenge پروفایل جدا لازم است.

پورت‌های برنامه و subnetهای Docker پیش از نصب بررسی می‌شوند؛ در نصب جدید در صورت تداخل، بلوک آزاد انتخاب و
در تنظیمات همان محیط ثبت می‌شود. سرویس یا شبکهٔ پروژهٔ دیگری متوقف یا حذف نمی‌شود.

بررسی اولیهٔ منابع، برای پروفایل دارای dev سرور کلاس ۴ GiB و برای پروفایل بدون dev کلاس ۲ GiB در نظر می‌گیرد.
برای تفاوت واحد ظرفیت و حافظهٔ رزروشده، حداقل رم قابل مشاهدهٔ Linux برابر ۹۰٪ این مقدار است؛ بنابراین
نمایش ۳٫۸ GiB روی سرور کلاس ۴ گیگ مانع نصب نیست. معیار `MemTotal` است و swap جایگزین آن نمی‌شود.
حداقل فضای خالی دیسک همچنان ۱۰ GiB است.

اگر Docker را همین نصاب نصب کند، `/etc/docker/daemon.json` با `max-size=10m` و `max-file=5` نوشته و Docker
یک بار ری‌استارت می‌شود تا لاگ کانتینرها روی دیسک ۱۰ گیگی جمع نشود. اگر `daemon.json` از قبل وجود داشته
باشد بازنویسی نمی‌شود؛ روی سروری که موتور Docker آن قبلاً نصب شده، تنظیم چرخش لاگ به اپراتور واگذار
می‌شود و نصاب پیام یادآوری چاپ می‌کند.
 عبور از این بررسی، تضمین ظرفیت زیر بار یا اجرای هم‌زمان همهٔ محیط‌ها نیست.

نصاب سرویس‌های موجود روی ۸۰/۴۴۳ و Caddyfile سفارشی را بازنویسی نمی‌کند. اگر Caddy از قبل دارید، import اعلام‌شده را در فایل خود وارد کنید و سپس نصب را ادامه دهید. پورت SSH تغییر نمی‌کند؛ پیش از فعال‌شدن firewall، گوش‌دادن پورت انتخاب‌شده بررسی می‌شود و قانون همان پورت اضافه می‌شود. قوانین موجود firewall حذف نمی‌شوند. محدودیت شبکهٔ پنل VPS هم باید SSH و ۸۰/۴۴۳ را اجازه دهد.

ابزار و templateهای لازم در `/opt/mushroom-installer/` نصب می‌شوند و پس از نصب می‌توان از `sudo mushroom ...` استفاده کرد. وضعیت مراحل در `/etc/mushroom/installation.json` است. اجرای مجدد سکرت، رمز مدیر یا تنظیمات موجود را جایگزین نمی‌کند؛ فایل مفقود یا نامعتبر باید بررسی شود. برای تغییر تنظیمات runtime موجود، فایل عمومی همان محیط را اصلاح کنید و workflow مربوط را اجرا کنید؛ تغییر کلید رمزنگاری از مسیر rotation مستند انجام شود.

## آماده‌سازی خودکار چت و ارتقای نصب قبلی

نصب اولیه فایل‌های Compose چت، کلید RSA و JWK، رمزها، دیتابیس خصوصی موتور، ایمیج موتور و مسیر WebSocket در Caddy را آماده می‌کند. سورس محدود ساخت موتور در بسته موجود است؛ clone ریپوی خصوصی یا Node روی سرور لازم نیست. اولین ساخت به رجیستری‌های عمومی Docker دسترسی می‌خواهد.

برای سرور نصب‌شده، بستهٔ جدید را پس از ادغام تغییرات نصاب/استقرار در مخزن منتقل و checksum آن را بررسی کنید. سپس از پوشهٔ بسته اجرا کنید:

```bash
sudo bash installation/install.sh setup-chat --environment dev
```

این فرمان ابزار `/opt/mushroom-installer` را هم به‌روز می‌کند. برای اجرای دوباره بعدی، `sudo mushroom setup-chat --environment dev` کافی است. پس از موفقیت، workflow `Sync shared dev VPS` را دوباره اجرا و سپس `sudo mushroom verify` را اجرا کنید. آماده‌سازی چت، موفقیت deploy برنامه یا پذیرش واقعی پیام‌رسانی را پیش از اجرای آن‌ها اعلام نمی‌کند. سایر محیط‌ها با همان `--environment` انتخاب می‌شوند؛ حذف این گزینه همهٔ محیط‌های ثبت‌شده را آماده می‌کند.

رمزها و کلیدهای موجود حفظ می‌شوند و اجرای مجدد دادهٔ چت را حذف نمی‌کند. اگر کلید خصوصی یا credential محیط آماده‌شده گم شده باشد، ابزار بازیابی نسخهٔ اصلی را درخواست می‌کند. تنظیمات قبلی دیتابیس یا ایمیج خارجی حفظ می‌شوند؛ دیتابیس خارجیِ از قبل تنظیم‌شده باید توسط مالک آن آماده شده باشد.

برای محیط تازه، PostgreSQL چت در کانتینر `chat-postgres` و volume اختصاصی همان Compose project ساخته می‌شود؛ پورتی از آن منتشر نمی‌شود و اتصال دیتابیس برنامه تغییر نمی‌کند. فایل `chat.database.yaml` باید در تمام deploy/rollback/operations همراه overlay چت بماند. ایمیج ساخته‌شدهٔ نصاب با `pull_policy: never` حفظ می‌شود؛ چهار ایمیج برنامه همچنان از مسیر انتشار قبلی می‌آیند. آرشیو موتور در volume جداست و بکاپ فعلی دیتابیس برنامه آن را پوشش نمی‌دهد؛ برای بازیابی کامل پیام‌رسانی باید از این دیتابیس نیز بکاپ مستقل نگه داشت.

## فایل‌های هر محیط

```text
/home/mushroom-deploy/mushroom/dev/
/home/mushroom-deploy/mushroom/staging/
/home/mushroom-prod/mushroom/production/
  runtime.env                 تنظیمات عمومی
  network.yaml                شبکهٔ اختصاصی و gateway مشخص proxy
  secrets/                    سکرت‌های محیط
  bootstrap/first-run.json    فقط تا اولین ساخت موفق مدیر
  bootstrap/complete          نشانگر موفقیت bootstrap
  consent-policy.json         فقط production، سند تأییدشدهٔ مالک
  releases/                   نسخه‌های immutable؛ پس از CD
  state/                      نسخهٔ جاری/قبلی؛ پس از CD
```

سکرت‌ها با دسترسی 0600 ساخته می‌شوند. helper انتشار برای Distroless، فقط گروه runtime لازم را با 0640 مجاز می‌کند؛ مقادیر world-readable نمی‌شوند. رمز مدیر فقط به helper یک‌باره mount می‌شود، وارد runtime عادی API، GitHub یا log نمی‌شود و پس از ساخت موفق از سرور حذف می‌شود. bootstrap کلینیک/مدیر را transactionally ایجاد می‌کند؛ retry رمز را عوض نمی‌کند و وجود کاربر قبلی یا مدیر متفاوت را رد می‌کند. دادهٔ پزشکی/فروشگاهی یا رضایت‌نامهٔ ساختگی production seed نمی‌شود. دادهٔ نمونه فقط در dev باقی می‌ماند.

مدیر اولیه با شمارهٔ واردشده هنگام نصب ساخته می‌شود و هم‌زمان عضویت همان کلینیک را برای دسترسی‌های محدود به کلینیک، از جمله کنترل کیف پول، دریافت می‌کند. حساب، نقش، عضویت و audit در یک تراکنش ثبت می‌شوند؛ هیچ شماره یا شناسهٔ مدیر ثابتی وجود ندارد. اجرای مجدد روی حساب موجود، رمز یا عضویت‌های بعداً تغییرکرده را بازنویسی نمی‌کند؛ اصلاح دسترسی حساب‌های نصب‌شدهٔ قبلی باید جداگانه و با تأیید مالک انجام شود.

سند رضایت production باید دقیقاً قرارداد `apps/api/prisma/manage-consent-policy.mjs` را داشته باشد: `schemaVersion: 1`، `policyKey: MEDICAL_INTAKE_PROCESSING`، `versionNumber` مثبت، `locale: fa-IR`، عنوان و body تأییدشده، `contentHash` برابر SHA-256 بایت‌های UTF-8 خود body و `allowedEnvironments` شامل `production`. فایل rehearsal قابل استفادهٔ production نیست. این سند را مالک پیش از نصب آماده و جداگانه منتقل می‌کند؛ نصاب متن حقوقی تولید نمی‌کند. دیگر رضایت‌ها و تنظیمات محتوایی محصول همچنان از مسیرهای مدیریتی مربوط منتشر می‌شوند.

## اتصال دستی GitHub

بعد از آماده‌شدن DNS/TLS:

```bash
sudo mushroom doctor
sudo mushroom github
```

گزارش نام و محل مقدارها در `/etc/mushroom/github/GITHUB-SETUP.txt` است. **کلید خصوصی به‌صورت خودکار چاپ نمی‌شود.** آن را از فایل محافظت‌شدهٔ اعلام‌شده با کانال امن بردارید و فقط در GitHub Secret مربوط قرار دهید. کلید عمومی با محدودیت‌های SSH در حساب استقرار نصب شده است؛ کلید `known_hosts` مستقیماً از host key همان سرور تهیه می‌شود. fingerprint را هنگام تحویل با هویت سرور تطبیق دهید.

Repository Secrets برای dev/staging:

- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_PRIVATE_KEY`
- `VPS_KNOWN_HOSTS`

Repository Secrets مستقل production:

- `PRODUCTION_VPS_HOST`
- `PRODUCTION_VPS_USER`
- `PRODUCTION_VPS_SSH_PRIVATE_KEY`
- `PRODUCTION_VPS_KNOWN_HOSTS`

Repository Variables:

- `VPS_PORT` و `PRODUCTION_VPS_PORT`: پورت SSH.
- `VPS_DEV_CD_ENABLED`: فعال‌سازی dev.
- `VPS_CD_ENABLED`: فعال‌سازی staging.
- `PRODUCTION_CD_ENABLED`: فعال‌سازی production.
- `PRODUCTION_AUTO_DEPLOY`: پیش‌فرض `false`؛ با انتخاب صریح انتشار خودکار در نصب، `true` می‌شود.
- `RELEASE_APPROVER`: username GitHub مالک انتشار.
- `GHCR_ATTESTATIONS_ENABLED=false`: پیش‌فرض؛ فقط با پشتیبانی حساب فعال شود.

پرچم‌ها را بعد از موفقیت `doctor` فعال کنید. GitHub Actions برای checkout ریپوی خصوصی و pull ایمیج‌های خصوصی از `GITHUB_TOKEN` کوتاه‌عمر با permission لازم استفاده می‌کند؛ PAT دائمی روی VPS قرار نمی‌گیرد. packageهای GHCR خصوصی و قابل دسترسی از Actions همین repository باشند. مقدارهای برنامه مانند JWT، رمز دیتابیس و کلیدهای پیامک/پرداخت روی سرور باقی می‌مانند. دسترسی نوشتن به branch/workflow و مقدار `RELEASE_APPROVER` را فقط به افراد مجاز بدهید.

پس از ثبت مقادیر:

1. dev: در Actions، `Sync shared dev VPS` را روی branch `dev` اجرا کنید؛ pushهای بعدی همان branch sync می‌شوند.
2. staging: merge/push روی `main`، سپس CI موفق و `Release and stage`؛ چهار ایمیج ساخته و بررسی می‌شوند و manifest ثبت می‌شود.
3. production: در حالت پیش‌فرض، `Deploy production` را اجرا و `release_run_id` همان اجرای موفق staging را وارد کنید. فقط `RELEASE_APPROVER` می‌تواند دستی منتشر کند. در صورت انتخاب صریح حالت automatic و تنظیم متغیر مربوط، هر staging موفق روی main خودکار همان digestها را به production می‌فرستد؛ staging ناموفق یا خارج از مخزن/branch مجاز منتشر نمی‌شود.
4. پس از استقرار: `sudo mushroom verify` سلامت بیرونی HTTPS را بررسی می‌کند. آزمون واقعی ورود، providerها و پذیرش محصول همچنان جداست.

ثبت Secret به‌تنهایی workflow را اجرا نمی‌کند. انتشار production به‌صورت پیش‌فرض تأیید دستی دارد و حالت خودکار باید هنگام نصب و در GitHub صریحاً فعال شود. rollback از workflow `Production rollback` برای production و `Application rollback` برای rehearsal انجام می‌شود؛ فقط برنامه به ایمیج قبلی برمی‌گردد و دیتابیس reset نمی‌شود. در این توپولوژی تک‌میزبان، zero-downtime تضمین نشده است.

زنجیرهٔ خودکار `CI → Release and stage → Deploy production` از رویداد رسمی
[`workflow_run`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)
استفاده می‌کند؛ فایل workflow باید در branch پیش‌فرض وجود داشته باشد.

بعد از تست دسترسی Actions، کپی خصوصی تحویل را از مسیر اعلام‌شده حذف کنید؛ فایل `.pub` را نگه دارید. نصب مجدد کلید عمومی موجود را حفظ می‌کند و کلید تازه‌ای برای آن حساب نمی‌سازد. تعویض عمدی کلید SSH/رمزنگاری باید با مسیر rotation و به‌روزرسانی هماهنگ GitHub انجام شود.

## پنل تعاملی

اگر نصب قبلاً انجام شده باشد، اجرای ابزار **بدون آرگومان** یک پنل متنی کوتاه باز می‌کند:

```bash
sudo mushroom
```

روی سرور تازه که `/etc/mushroom/installation.json` ندارد، همین فرمان همان نصب اولیه را شروع می‌کند.
پنل منطق تازه‌ای ندارد؛ هر گزینه فقط زیرفرمان موجود را صدا می‌زند، پس رفتار CLI و پنل یکی است:

| کلید | عمل در پنل | برابر CLI |
|---|---|---|
| `1` | وضعیت کانتینرها | `sudo mushroom status --environment <env>` |
| `2` | ۱۰۰ خط آخر لاگ | `sudo mushroom logs --environment <env>` |
| `3` | بررسی‌های سلامت | `sudo mushroom doctor` |
| `4` | راه‌اندازی دوبارهٔ محیط | `sudo mushroom restart --environment <env>` |
| `5` | آماده‌سازی موتور چت و پروکسی | `sudo mushroom setup-chat --environment <env>` |
| `6` | تغییر دامنه/runtime | `sudo mushroom configure --environment <env>` |
| `7` | ذخیره‌سازی شیء dev | `sudo mushroom setup-storage --environment dev` |
| `8` | بکاپ رمزنگاری‌شدهٔ فوری | `sudo mushroom backup --environment <env>` |
| `9` | خلاصهٔ اتصال GitHub | `sudo mushroom github` |
| `10` | اعمال دوبارهٔ پروکسی/TLS | بازتولید `mushroom.caddy` و reload کدی |
| `11` | ادامهٔ نصب | `sudo mushroom install` |
| `q` | خروج | — |

قواعد ایمنی پنل:

- فقط در ترمینال واقعی (TTY) بالا می‌آید؛ در اجرای غیرتعاملی/اسکریپتی با پیام روشن رد می‌شود و باید
  از زیرفرمان استفاده کنید.
- گزینه‌های `4`، `5`، `10` و `11` پیش از اجرا **تأیید تایپی** نام محیط (یا عبارت `install` برای
  نصب کامل) می‌خواهند؛ تأیید اشتباه هیچ تغییری ایجاد نمی‌کند.
- هر انتخاب با زمان، شناسهٔ گزینه و محیط در `/etc/mushroom/panel.log` با دسترسی `0600` ثبت می‌شود.
  هیچ مقدار سکرت نوشته یا روی صفحه نمایش داده نمی‌شود؛ پنل فقط نشانگرهای آماده‌سازی و استقرار
  (`runtime.env`، `state/current`، `state/chat.initialized`، `bootstrap/first-run.json`) و حالت
  ذخیره‌سازی را می‌خواند.
- برچسب‌ها انگلیسی و ASCII هستند تا در ترمینال‌های بدون پشتیبانی راست‌به‌چپ سالم بمانند؛ توضیح فارسی
  همین بخش است.

## مدیریت و بکاپ

```bash
sudo mushroom status --environment dev
sudo mushroom logs --environment production
sudo mushroom restart --environment production
sudo mushroom configure --environment dev
sudo mushroom setup-storage --environment dev
sudo mushroom setup-analytics --environment dev
sudo mushroom backup --environment production
sudo mushroom restore --environment production --archive /var/backups/mushroom/production/BACKUP.dump.age --target-url-file /root/restore-test-url
```

فرمان `configure` اتصال SSH، دامنه‌ها و حالت‌های runtime را تعاملی تنظیم می‌کند؛ برای اصلاح فقط اطلاعات اتصال،
آن را بدون `--environment` اجرا کنید. تنظیمات دامنه پیش از ذخیره با DNS بررسی می‌شود. در dev، انتخاب فضای
ذخیره‌سازی و دریافت مخفی کلیدهای آن هم در همین فرمان است. پس از تغییر runtime، workflow همان محیط را
اجرا کنید تا کانتینرها تنظیم تازه را بگیرند.

## تنظیم امن GA4 در Dev

پس از نصب نسخهٔ به‌روز نصاب، `sudo mushroom setup-analytics --environment dev` را اجرا کنید
یا گزینهٔ «Configure dev analytics» پنل را انتخاب کنید. حالت `disabled`، فقط `browser` یا
`server` را انتخاب کنید؛ شناسهٔ استریم Dev فعلی `G-KXRZ89EFTY` است. حالت‌های فعال فقط برای
جمع‌آوری مجاز در Dev هستند. DebugView به‌صورت یک انتخاب مستقل پرسیده می‌شود.

در حالت `server`، API Secret همان استریم از ورودی مخفی دریافت می‌شود و در فایل
`<DEV_ROOT>/secrets/ga4_api_secret` با دسترسی `0600` و مالک محیط Dev ذخیره می‌شود.
Secret در `runtime.env`، وضعیت نصاب، آرگومان فرمان، Git یا خروجی نوشته نمی‌شود.
Secret موجود بدون انتخاب صریح جایگزین نمی‌شود؛ غیرفعال‌کردن آنالیتیکس فایل را حذف نمی‌کند.
این فرمان برای Production یا سایر محیط‌ها پذیرفته نمی‌شود.

پس از Merge این تغییر و نصب نصاب جدید، تنظیمات را ذخیره و workflow همگام‌سازی Dev را
دوباره اجرا کنید؛ تغییر تنظیمات یا Secret باعث بازسازی runtime مربوط می‌شود. صرفاً
`restart` تنظیمات Compose جدید را وارد کانتینر موجود نمی‌کند. سپس دریافت واقعی رویدادهای
مرورگر و بک‌اند را طبق `config/ANALYTICS-ACCEPTANCE.md` بررسی کنید. موفقیت نصاب به معنی
تأیید دریافت در GA4 نیست؛ Production به‌صورت پیش‌فرض خاموش می‌ماند.

## انتخاب Cloudflare یا آروان برای تصاویر

نصب اولیهٔ dev و `configure --environment dev` اکنون این سه انتخاب را می‌پرسند:

| انتخاب | سرویس | تنظیمات و فایل کلید |
| --- | --- | --- |
| `disabled` | غیرفعال | پیش‌فرض؛ فایل‌های قبلی حفظ می‌شوند |
| `r2` | Cloudflare R2 | `R2_*` و `secrets/r2_access_key_id`، `secrets/r2_secret_access_key` |
| `arvancloud` | Object Storage آروان | `ARVAN_*` و `secrets/arvan_access_key_id`، `secrets/arvan_secret_access_key` |

برای تغییر فقط ذخیره‌سازی، بدون پرسش دوباره دربارهٔ SSH، دامنه‌ها و DNS، از `setup-storage` استفاده کنید.
endpoint منطقهٔ bucket خصوصی، نام bucket، region و timeout پرسیده می‌شوند. region برای R2 برابر `auto`
و پیش‌فرض آروان `default` است. کلیدها با ورودی مخفی دریافت و با دسترسی `0600` و مالک محیط ذخیره می‌شوند؛
در `runtime.env` یا `installation.json` نوشته نمی‌شوند. جایگزینی هر کلید موجود تأیید جداگانه می‌خواهد؛
تنظیمات و کلیدهای سرویس غیرفعال حذف نمی‌شوند. نصب مجدد، تنظیمات ذخیره‌شده را بدون درخواست تغییر نمی‌دهد.

برای سروری که منیجر قدیمی دارد، بستهٔ جدید را بسازید و دو فایل آرشیو و checksum را منتقل کنید. پس از
بررسی checksum و استخراج، از پوشهٔ بسته اجرا کنید:

```bash
sudo bash installation/install.sh setup-storage --environment dev
```

این فرمان نسخهٔ نصب‌شدهٔ ابزار را هم به‌روز می‌کند. برای دفعات بعد:

```bash
sudo mushroom setup-storage --environment dev
```

این مرحله فقط تنظیمات را ذخیره می‌کند؛ برنامه را deploy نمی‌کند و موفقیت اتصال S3 را تأیید نمی‌کند.
کد پشتیبانی از provider انتخاب‌شده باید روی dev مستقر شود؛ سپس workflow `Sync shared dev VPS` را اجرا
کنید تا API تنظیمات را بگیرد. restart ساده برای تغییر environment کافی نیست.

تعویض بین دو سرویس فعال فایل‌ها را منتقل نمی‌کند. منیجر پیش از تعویض، تأیید انتقال فایل‌ها با همان
object key و توقف موقت نوشتن/پاک‌سازی، یا خالی‌بودن محیط را می‌گیرد. جزئیات انتقال و تست واقعی در
[راهنمای ذخیره‌سازی](../deploy/vps/OBJECT-STORAGE.md) است. این قابلیت فعلاً برای dev است؛ محدودیت قبلی
غیرفعال‌بودن ذخیره‌سازی در production حفظ می‌شود.

در صورت انتخاب بکاپ، timer روزانهٔ systemd به وقت UTC نصب می‌شود. خروجی `pg_dump` مستقیم با age رمز می‌شود؛ dump بدون رمز روی دیسک ذخیره نمی‌شود. فایل‌ها در `/var/backups/mushroom/<environment>/` و کلید خصوصی age در `/etc/mushroom/backup-keys/` هستند. **نسخهٔ خارج سرور از آرشیو و نسخهٔ جدا از کلید لازم است.** این بکاپ منطقی جایگزین WAL/PITR، سیاست RPO/RTO یا پذیرش قابلیت provider نیست. مدت نگهداری محلی هنگام نصب دریافت می‌شود (پیش‌فرض ۱۴ روز)؛ فقط پس از بکاپ موفق، آرشیوهای قدیمی با نام تولیدشدهٔ خود ابزار پاک می‌شوند. انتقال خارج سرور و پایش فضا همچنان باید مطابق سیاست عملیاتی انجام شود.

restore فقط دیتابیس از قبل ساخته‌شده، خالی و مجزایی را می‌پذیرد که نامش به `_restore_test` ختم شود؛ مقصد اصلی و دیتابیس دارای جدول رد می‌شوند. URL مقصد داخل فایل 0600 قرار می‌گیرد؛ روی خط فرمان وارد نمی‌شود. برای production نسخهٔ client بکاپ باید با major واقعی PostgreSQL هماهنگ باشد. URLهای دارای CA خصوصی/گزینه‌های اتصال اضافی به تنظیم اختصاصی provider نیاز دارند؛ failure موفق گزارش نمی‌شود. نتیجهٔ timer و نشانگر `last-success` باید پایش شوند؛ ارسال هشدار بیرونی به اتصال سرویس هشدار نیاز دارد.

SMS و سداد به‌صورت پیش‌فرض disabled هستند. انتخاب live فقط کنترل runtime را آماده می‌کند؛ credentialهای provider و enable/default از تنظیمات امن Dashboard انجام می‌شوند. پذیرندگی سداد، ثبت callback، IP خروجی ثابت و تراکنش پذیرش خودکار جعل نمی‌شوند. تصاویر R2 در production مطابق policy فعلی برنامه غیرفعال می‌مانند.

## بررسی تنظیمات پروژه و تست نصاب

```console
python -B installation/manager.py audit-env --current
python -B -m unittest discover -s installation -p "test_*.py" -v
```

Audit فقط نام کلیدهای غایب/نامعتبر را گزارش می‌کند و فایل‌های واقعی `.env` را تغییر نمی‌دهد. نصاب به `.env`های لپ‌تاپ وابسته نیست. تطبیق با schema واقعی API و Composeها در تست کنترل می‌شود. اجرای آزمایشی روی VPS خالی، دو انتشار متوالی، بازیابی بکاپ و پذیرش providerها پیش از دادهٔ واقعی الزامی است؛ تست‌های fixture معادل پذیرش سرور واقعی نیستند.
