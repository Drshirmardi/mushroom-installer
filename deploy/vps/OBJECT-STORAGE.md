# انتخاب فضای ذخیره‌سازی تصاویر

انتخاب سرویس یک تنظیم سرور است و برای تصاویر محصول و بنرها مشترک است. در هر محیط فقط یک سرویس
فعال می‌شود. پر بودن تنظیمات سرویس دیگر، آن را فعال نمی‌کند و شکست سرویس فعال هم باعث سوییچ نمی‌شود.
فرانت همچنان تصویر را از API خودمان می‌گیرد؛ کلیدها هرگز در `NEXT_PUBLIC_*` قرار نمی‌گیرند.

| `PRODUCT_MEDIA_STORAGE_MODE` | سرویس فعال | تنظیمات خوانده‌شده |
| --- | --- | --- |
| `disabled` | غیرفعال؛ مقدار پیش‌فرض | هیچ اتصال ذخیره‌سازی |
| `r2` | Cloudflare R2 | فقط `R2_*` |
| `arvancloud` | فضای ابری S3 آروان | فقط `ARVAN_*` |

`arvan` یا `cloudflare` مقدار معتبر نیستند. نام قدیمی `r2` برای سازگاری با محیط‌های موجود حفظ شده است.
محدودیت قبلی production حفظ شده: در `NODE_ENV=production` فقط `disabled` پذیرفته می‌شود؛ این تغییر
برای تست محلی و shared dev است و مجوز فعال‌سازی production نیست.

## تست محلی آروان

در پنل آروان از بخش **فضای ابری / Object Storage** یک bucket خصوصی ایجاد کنید؛ File Storage/NFS
سرویس موردنیاز این پروژه نیست. کلید باید مجوز نوشتن، خواندن و حذف object در همان bucket داشته باشد.
endpoint را از منطقهٔ همان bucket بردارید؛ نمونهٔ سیمین `https://s3.ir-thr-at1.arvanstorage.ir` است.

این تنظیمات را در فایل نادیده‌گرفته‌شدهٔ `apps/api/.env` بگذارید. مقدارهای داخل `<...>` نمونه‌اند:

```dotenv
PRODUCT_MEDIA_STORAGE_MODE=arvancloud
ARVAN_ENDPOINT=https://s3.ir-thr-at1.arvanstorage.ir
ARVAN_BUCKET=<your-private-dev-bucket>
ARVAN_REGION=default
ARVAN_ACCESS_KEY_ID=<your-arvan-access-key>
ARVAN_SECRET_ACCESS_KEY=<your-arvan-secret-key>
ARVAN_REQUEST_TIMEOUT_MS=5000
```

`default` مطابق نمونهٔ رسمی JavaScript آروان است. اتصال آروان از path-style و checksum فقط در صورت
الزام استفاده می‌کند. اطلاعات R2 را می‌توانید نگه دارید؛ هنگام انتخاب آروان استفاده نمی‌شوند.
متغیرهای exportشدهٔ shell بر `.env` اولویت دارند؛ قبل از تست سرویس انتخاب‌شده را بررسی کنید.

از ریشهٔ مخزن اجرا کنید؛ این دستور با همان adapter برنامه یک WebP تک‌پیکسلی با کلید تصادفی می‌نویسد،
آن را می‌خواند و بایت‌ها را مقایسه می‌کند، سپس حذفش می‌کند. دیتابیس تغییر نمی‌کند:

```bash
pnpm --filter @sabz-zendegi/api storage:smoke --confirm-write
```

انتظار: `Provider: arvancloud` و سه نتیجهٔ `PUT`، `GET + byte comparison` و `DELETE` برابر `OK`.
اگر حذف شکست خورد، نام object موقت چاپ می‌شود تا همان فایل را از پنل پاک کنید. برای Cloudflare همین
تست را با `PRODUCT_MEDIA_STORAGE_MODE=r2` و تنظیمات قبلی `R2_*` اجرا کنید.

بعد API محلی را مجدداً راه‌اندازی و از داشبورد تصویر محصول و بنر را آپلود، مشاهده، جایگزین و حذف کنید.
آزمون SDK به‌تنهایی پذیرش کامل مسیر داشبورد نیست. پس از تأیید محلی، تغییر به شاخهٔ dev منتقل می‌شود.

## سرور shared dev

در `<dev-root>/runtime.env` فقط تنظیمات غیرمحرمانه را قرار دهید:

```dotenv
PRODUCT_MEDIA_STORAGE_MODE=arvancloud
ARVAN_ENDPOINT=https://s3.ir-thr-at1.arvanstorage.ir
ARVAN_BUCKET=<your-private-dev-bucket>
ARVAN_REGION=default
ARVAN_REQUEST_TIMEOUT_MS=5000
```

دو فایل زیر باید فقط حاوی مقدار کلید مربوطه باشند و توسط کاربر اجرای API قابل خواندن باشند:

```text
<dev-root>/secrets/arvan_access_key_id
<dev-root>/secrets/arvan_secret_access_key
```

Compose مسیر فایل‌ها را با `ARVAN_ACCESS_KEY_ID_FILE` و `ARVAN_SECRET_ACCESS_KEY_FILE` به API می‌دهد.
فایل‌های `runtime.env` و کلیدها وارد Git نمی‌شوند. deploy عادی dev تغییر تنظیمات و اثرانگشت فایل کلیدها
را تشخیص می‌دهد و API را بازسازی می‌کند؛ restart سادهٔ کانتینر برای تغییر environment کافی نیست.
از مسیر واقعی نصب استفاده کنید؛ در نصب فعلی مالک، dev-root برابر
`/home/mushroom-deploy/mushroom/dev` است. کلیدها روی سرور جداگانه تنظیم می‌شوند و با push منتقل نمی‌شوند.

## تعویض سرویس با تصاویر قبلی

این قابلیت انتخاب سرویس است، انتقال یا تکثیر فایل نیست. دیتابیس و رویدادهای حذف فقط object key دارند.
برای تغییر سرویسِ محیطی که تصویر دارد:

1. آپلود/جایگزینی/حذف تصویر و worker پاک‌سازی را در پنجرهٔ نگه‌داری متوقف کنید.
2. از مبدأ نسخهٔ پشتیبان بگیرید و همهٔ objectهای محصول و بنر را با همان key به مقصد کپی کنید.
3. کامل بودن فایل‌ها و محتوای آن‌ها را تأیید کنید؛ objectهای در انتظار پاک‌سازی هم باید لحاظ شوند.
4. تنظیمات مقصد و mode را تغییر دهید، API و workerها را با یک تنظیم مشترک راه‌اندازی کنید.
5. خواندن تصویر قدیمی و آپلود/جایگزینی/حذف جدید را بررسی کنید؛ مبدأ را تا پایان پذیرش نگه دارید.

اگر مقصد خالی باشد، تصاویر قبلی با تغییر mode قابل خواندن نخواهند بود. برای bucket تازه و بدون تصویر
قبلی، انتقال لازم نیست. نگهداری فایل‌های قدیمی روی یک سرویس و جدیدها روی سرویس دیگر به ثبت محل هر
object در دیتابیس و outbox نیاز دارد و جزو این تغییر نیست.

## خطایابی

پاسخ عمومی API همان `COMMERCE_IMAGE_STORAGE_UNAVAILABLE` است. لاگ سرور اکنون نام سرویس، عملیات
`put/read/delete` و نام/کد خطا و HTTP status موجود را ثبت می‌کند؛ متن خام خطا، object key و کلیدها
ثبت نمی‌شوند. خطای شبکهٔ R2 در سرور با اضافه‌شدن آروان تعمیر نمی‌شود؛ ارتباط آروان نیز باید از خود
سرور و داخل کانتینر آزمایش شود.

منابع: [SDK آروان](https://docs.arvancloud.ir/fa/developer-tools/sdk/object-storage/credentials/)،
[S3 در R2](https://developers.cloudflare.com/r2/get-started/s3/).
