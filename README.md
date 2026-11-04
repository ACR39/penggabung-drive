# 🗂️ Penggabung Drive

Gabungkan beberapa akun Google Drive menjadi **satu penyimpanan virtual**, dibuat dengan Python + Streamlit.

Konsepnya terinspirasi dari [9Drive](https://github.com/zenhosta/9drive) (Apache-2.0). Kode di sini ditulis ulang dari nol, tidak menyalin kode 9Drive.

## Fitur

- **Gabung banyak akun Google Drive**: total kuota, terpakai, dan sisa dalam satu dasbor.
- **Unggah otomatis ke akun yang muat**: tiga aturan, yaitu *ruang terbanyak*, *bergantian*, dan *urutan prioritas*. Bisa juga memilih akun tujuan sendiri.
- **Folder virtual bersarang** dan **pencarian** di semua akun. Folder hanya ada di database, berkas di Drive disimpan rata di folder `PenggabungDrive`.
- **Tautan berbagi publik** dengan masa berlaku (1, 7, 30 hari atau tanpa batas) dan bisa dimatikan.
- Ganti nama, pindah, sampah, pulihkan, hapus permanen, dan **sinkronkan** berkas yang ditaruh manual di folder `PenggabungDrive`.
- Token Google disimpan **terenkripsi**. Unggahan tidak ditulis ke disk server.

## 1. Siapkan Google Cloud (sekali saja, ±10 menit)

1. Buka <https://console.cloud.google.com/> dan buat/pilih proyek.
2. *APIs & Services → Library → Google Drive API → Enable*.
3. *OAuth consent screen*: tipe **External**, isi nama aplikasi dan email. Tambahkan scope:
   `.../auth/drive`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`.
   Selama status **Testing**, daftarkan semua akun Google yang akan dipakai di **Test users**.
4. *Credentials → Create Credentials → OAuth client ID → Web application*.
   - **Authorized redirect URIs**: isi **persis sama** dengan `APP_URL`, misalnya `http://localhost:8501` (lokal) atau `https://namaapp.streamlit.app` (online). Tanpa garis miring di akhir.
5. Salin **Client ID** dan **Client Secret**.

## 2. Jalankan di komputer sendiri

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# isi GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, TOKEN_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # contoh pembuat kunci
streamlit run app.py
```

Buka <http://localhost:8501>, klik **Masuk dengan Google**, lalu setujui semua izin.

## 3. Pasang online (Streamlit Community Cloud)

1. Unggah folder ini ke repositori GitHub (boleh privat). `secrets.toml` sudah masuk `.gitignore`.
2. Di <https://share.streamlit.io> buat app dari repositori itu, file utama `app.py`.
3. Isi **Settings → Secrets** dengan isi `secrets.toml.example`, dan set `APP_URL` ke alamat app Anda.
4. Tambahkan alamat itu ke **Authorized redirect URIs** di Google Cloud.
5. **Wajib pakai database Postgres.** Lihat bagian *Memakai Supabase* di bawah.
   Alasannya: Disk Streamlit Cloud tidak permanen, jadi SQLite akan hilang saat app restart. Gunakan Postgres gratis (Supabase atau Neon) dan isi `DATABASE_URL=postgresql://...`.

## Cara pakai singkat

1. Masuk dengan akun Google utama. Akun itu otomatis terhubung.
2. **Pengaturan → Hubungkan akun Google lain** untuk menambah akun (pilih akun lain saat Google bertanya).
3. Di menu **Kuota**, atur **Aturan unggah**: pilih mode, lalu ubah urutan akun dengan tombol Naik/Turun.
4. **Unggah** berkas, **Berkas** untuk melihat, mencari, memindah, dan membagikan.

## Memakai Supabase (Postgres gratis)

1. Buat proyek di supabase.com, simpan *database password*, dan pilih **region** yang dekat dengan server aplikasi.
2. Klik **Connect** di dasbor proyek, ambil **Session pooler** (host `...pooler.supabase.com`, port 5432). Jangan pakai koneksi langsung `db.xxx.supabase.co`: itu hanya IPv6 dan sering tidak terjangkau dari hosting.
3. Ganti `[YOUR-PASSWORD]` dengan password asli. Karakter khusus harus di-URL-encode (`@` jadi `%40`, `#` jadi `%23`, `/` jadi `%2F`, `:` jadi `%3A`), atau pakai password huruf dan angka saja.
4. Isi Secrets: `DATABASE_URL = "postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"`.
5. Restart aplikasi. Tabel dibuat otomatis. Proyek gratis bisa dijeda bila lama tidak dipakai.

## Kalau terasa lambat atau tidak bisa masuk

- Halaman **Masuk dengan Google** tidak memakai database, jadi harus langsung tampil. Bila setelah masuk muncul *"Tidak bisa terhubung ke database"*, periksa `DATABASE_URL` (poin di atas).
- Koneksi database diberi batas waktu 10 detik, jadi alamat yang salah akan gagal dengan pesan jelas, bukan menggantung.
- Dengan `DEBUG = "1"`, halaman masuk menampilkan **ping ke database** (ms). Di bawah 50 ms bagus, di atas 150 ms sebaiknya pindahkan region database.

- Isi `DEBUG = "1"` di Secrets. Di bawah sidebar akan tampil waktu total, jumlah kueri, dan waktu yang habis di database untuk setiap klik. Bila "ms di database" besar, penyebabnya jarak ke database.
- Pilih **region database yang dekat dengan server Streamlit** (lihat lokasi proyek di Supabase/Neon). Setiap klik memakai 1 sampai 3 kueri, jadi tiap 100 ms jarak menambah sekitar 0,1 sampai 0,3 detik.
- Di Supabase, pakai alamat **connection pooler**, bukan koneksi langsung.
- Kuota Google disegarkan **di latar belakang** (bila lebih dari 5 menit), jadi halaman tidak menunggu Google. Angka terbaru terlihat pada klik berikutnya. Tombol *Segarkan* menyegarkan semua akun sekaligus.

## Galat 403 dari Google Drive

- **"Insufficient Permission"**: layar izin Google memungkinkan satu per satu kotak izin tidak dicentang. Bila izin Drive tidak dicentang, token tidak bisa memakai Drive. Aplikasi sekarang memeriksanya saat masuk dan menandai akun sebagai *Hubungkan ulang*. Perbaikannya: **Pengaturan → Hubungkan akun Google lain**, pilih akun yang sama, lalu **centang semua kotak izin**. Bila layar izin tidak muncul lagi, buka <https://myaccount.google.com/permissions>, hapus akses "Penggabung Drive" (atau nama aplikasimu), lalu hubungkan ulang.
- **"Google Drive API has not been used in project..."**: Drive API belum diaktifkan di proyek Google Cloud yang sama dengan OAuth client. Aktifkan di *APIs & Services → Library → Google Drive API*, lalu tunggu beberapa menit.
- **"access_denied"** saat masuk: status layar persetujuan masih *Testing* dan akun belum didaftarkan di **Test users**.

## Batasan yang perlu diketahui

- **Login tidak bertahan saat halaman dimuat ulang.** Streamlit tidak menyimpan sesi. Klik *Masuk dengan Google* lagi, biasanya langsung masuk tanpa mengetik apa pun.
- **Unduhan dan tautan berbagi melewati server aplikasi** dan dimuat ke memori, jadi dibatasi `MAX_DOWNLOAD_MB` (bawaan 200 MB). Google Docs/Sheets diekspor sebagai PDF.
- Unggahan juga lewat memori server. Batas per berkas diatur `maxUploadSize` di `.streamlit/config.toml` (bawaan 1024 MB), tetapi hosting gratis punya RAM terbatas, jadi berkas sangat besar sebaiknya diunggah langsung ke Drive lalu disinkronkan.
- Tautan berbagi bisa dibuka siapa saja yang punya tautannya. Tidak ada pembatasan jumlah akses.
- Scope Drive yang dipakai adalah akses penuh (`drive`), agar sinkronisasi berkas manual bisa bekerja. Selama status *Testing* di Google, refresh token berlaku sekitar 7 hari, setelah itu akun perlu dihubungkan ulang. Terbitkan (*Publish*) layar persetujuan bila ingin lebih lama.
- Tidak ada pembagian ke pengguna lain (undangan) dan tidak ada S3 seperti di 9Drive.

## Menjalankan uji

```bash
python -m pytest -q          # logika inti (routing, folder, berbagi, sinkronisasi)
python tests/smoke_ui.py     # semua halaman tampil tanpa galat
```

## Struktur

```
app.py            antarmuka Streamlit
core/routing.py   aturan memilih akun (fungsi murni)
core/service.py   logika akun, upload, folder, berbagi
core/drive.py     pembungkus Google OAuth + Drive API
core/db.py        model database SQLAlchemy
core/crypto.py    enkripsi token
```

## Halaman Berkas (gaya "All Files")

- Kartu folder berwarna dengan ikon pilihan dan tanggal diperbarui (warna/ikon bisa diubah lewat "Ubah folder ini").
- Mode **Folder ini / Terbaru / Berbintang**, tampilan **Kisi / Daftar**, ukuran kartu **XS / SM / MD / LG**.
- Kartu berkas dengan kotak centang, ikon jenis berkas, tanggal, ukuran, dan akun asal.
- Tombol Unggah, Folder baru, dan Sinkron di bilah alat.
- Basis data lama otomatis diperbarui (kolom warna, ikon, bintang ditambahkan sendiri).
- Ikon dan warna dibuat sendiri, bukan salinan dari 9Drive.
