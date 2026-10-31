# PRD v2 - Oracle AWR Analyzer Skill

Oct 31, 2026 · @febridev

## 1. Ringkasan

Skill ini membaca satu laporan Oracle AWR dan menghasilkan temuan yang diurutkan berdasarkan severity (P0–P4), lengkap dengan bukti, SQL verifikasi, dan visual yang bisa langsung ditunjukkan ke user. Targetnya: satu DBA bisa menjawab permintaan "database kami lambat" dalam hitungan menit, bukan jam.

**Masalah yang diselesaikan**

- Satu DBA melayani banyak database dan banyak user, sehingga antrean permintaan cek performa panjang.
- AWR berisi puluhan section; memilah sinyal dari noise memakan waktu.
- Menjelaskan hasil ke user non-teknis sulit tanpa visual dan bahasa yang sederhana.
- SQL dari aplikasi user sulit dibedakan dari SQL milik monitoring atau maintenance database sendiri.

**Prinsip desain**

1. Angka dihitung oleh script, bukan oleh LLM. LLM menafsirkan hasil dan tidak membaca HTML mentah.
2. Setiap temuan wajib punya bukti: nama metrik, nilai, dan section AWR asalnya.
3. Fakta dan dugaan dipisahkan. Penyebab tanpa bukti langsung ditulis sebagai "Dugaan".
4. Skill hanya membaca file AWR. Tidak ada koneksi ke database dan tidak ada SQL yang dijalankan oleh skill.
5. Ambang batas disimpan di `thresholds.yaml`, bukan di-hardcode, supaya DBA bisa menyesuaikannya.

**Cara memakai dokumen ini.** Dokumen ini adalah spesifikasi untuk AI yang akan membuat skill. Keputusan bertanda **\[DEFAULT\]** adalah titik awal yang boleh diubah DBA. Bila ada hal yang tidak jelas, AI pembuat skill bertanya ke DBA dan tidak mengarang.

## 2. Goals dan Non-goals

Versi 1 mencakup delapan goal; tiap goal dipetakan ke kriteria penerimaan di bagian 15.

| ID | Goal | Hasil yang diharapkan |
| --- | --- | --- |
| G1 | Analisa header | DB name, DBID, instance, versi, single/RAC, CDB, host, jumlah CPU, memori, snapshot ID dan waktu, elapsed, DB time |
| G2 | Status beban | Database dinyatakan idle, normal, sibuk, jenuh, atau kritis berdasarkan rumus di bagian 6 |
| G3 | Severity | Setiap temuan berlabel P0–P4 dengan kriteria terukur (bagian 7) |
| G4 | Klasifikasi SQL | Top SQL dipisah: user, monitoring, maintenance, Oracle internal, unknown, masing-masing dengan alasan dan confidence |
| G5 | Analisa per domain | CPU, memory, disk I/O, redo, parsing, concurrency, dan RAC |
| G6 | SQL verifikasi | Tiap temuan membawa SQL read-only untuk memverifikasi dan menelusuri lebih lanjut |
| G7 | Laporan | `report.md` dan `report.html` (visual) |
| G8 | AWR diff | Menyarankan AWR diff saat relevan dan menyiapkan script-nya |

**Non-goals v1**

- Koneksi langsung ke database atau menjalankan SQL apa pun.
- Menerapkan perubahan (parameter, index, kill session). Skill hanya merekomendasikan.
- ASH report, ADDM report, dan SQL Monitor.
- AWR level PDB dan detail khusus Exadata. Keduanya hanya dideteksi dan diberi catatan.
- Tren banyak database sekaligus. Ini masuk v1.1.

## 3. Input

Input utama adalah satu file AWR berformat HTML; format lain diterima dengan akurasi lebih rendah.

| Aspek | Spesifikasi |
| --- | --- |
| Format | HTML dari `awrrpt.sql`, `awrgrpt.sql`, atau `AWR_REPORT_HTML` **\[DEFAULT\]**. Text (.txt/.lst) sebagai fallback. |
| Versi Oracle | 12.1 sampai 23ai **\[DEFAULT\]**; 11.2 best-effort. Konfirmasi versi yang kamu pakai. |
| Topologi | Single instance dan RAC (report per instance atau global `awrgrpt`). CDB ditandai dari header. |
| Cara input | Path file lampiran. Satu file per run di v1. |
| Ukuran | Hingga sekitar 50 MB. Parsing selalu lewat script; LLM tidak membaca file mentah. |
| Encoding dan locale | Encoding dideteksi otomatis. Parser menormalkan pemisah ribuan, sufiks K/M dan notasi ilmiah (153.2K, 3.1E+08, .8), satuan waktu campuran pada kolom Avg Wait (486.74us, 15.08ms), dan format tanggal NLS. Semua format ini muncul di sample 19c. |
| Parameter opsional | `user_schemas`, `monitoring_schemas`, `monitoring_modules`, `storage_profile` (hdd, san, flash), `lang` (`id` atau `en`, default `en`), `audience` (`dba`, `user`, `both`; default `both`), override ambang batas. |

**Validasi awal.** Bila file tidak memuat judul "WORKLOAD REPOSITORY", skill berhenti dan menjelaskan file apa yang diharapkan. Skill tidak menebak isi file.

**Identifikasi tabel.** Tabel di Report Summary tidak punya judul section sendiri, jadi parser mengenali setiap tabel dari atribut `summary` pada elemen `table` (misalnya "This table displays top 10 wait events by total wait time"). Judul h2/h3 hanya dipakai sebagai cadangan. Cara ini terverifikasi pada sample 19c yang berisi 97 tabel.

**Deteksi single atau RAC.** Skill membaca kolom RAC pada header dan jumlah instance pada report global. Ringkasan header selalu menjadi baris pertama laporan; nilai di bawah hanya contoh.

```text
DB: PRODDB (DBID 1234567890) | Instance 1 dari 2 (RAC) | 19.0.0.0.0 | CDB: YES
Host: dbhost01 | 32 CPU, 16 core, 256 GB | Linux x86 64-bit
Periode: snap 4120-4121 | 2026-10-30 09:00-10:00 | Elapsed 60.0 mnt | DB Time 245.3 mnt
```

## 4. Arsitektur pipeline

Pipeline berjalan satu arah dari file AWR ke laporan; script menghitung semua angka dan AI hanya menafsirkan hasilnya.

&#91;embedded content: pipeline analisa AWR · 6 langkah, 1 loop perbaikan\]

Pemeriksaan otomatis menolak temuan yang angkanya tidak cocok dengan `awr.json` atau SQL verifikasi yang tidak read-only. AI memperbaikinya dan mengulang pemeriksaan sebelum laporan dibuat.

## 5. Ekstraksi data

`parse_awr.py` mengubah AWR menjadi `awr.json`, dan semua angka di laporan berasal dari file ini. Section yang tidak ditemukan diberi nilai `null` beserta alasannya, lalu analisa tetap berjalan sebagian.

| Section AWR | Metrik wajib | Dipakai untuk |
| --- | --- | --- |
| Header / Snapshot Info | DB name, DBID, instance, startup time, release, RAC, CDB, host, platform, CPUs, cores, sockets, memori, snap ID, snap time, sessions, elapsed, DB time | G1, validasi periode |
| Load Profile | DB Time(s), DB CPU(s), redo size, logical reads, physical reads/writes, parses, hard parses, executes, transactions, user calls (per detik dan per transaksi) | Status beban, pola workload |
| Instance Efficiency | Buffer Hit %, Library Hit %, Soft Parse %, Execute to Parse %, Parse CPU to Parse Elapsd %, Latch Hit % | Parsing, shared pool |
| Top Foreground Events | Event, waits, total wait time, avg wait, % DB time, wait class | Bottleneck utama |
| Time Model | DB time, DB CPU, sql execute elapsed time, parse time elapsed, hard parse elapsed time, PL/SQL execution elapsed time | Rincian waktu |
| Host CPU, Instance CPU, Operating System Statistics | %User, %System, %WIO, %Idle; % DB time waiting for CPU (Resource Manager); LOAD, NUM\_CPUS, PHYSICAL\_MEMORY\_BYTES, VM\_IN\_BYTES, VM\_OUT\_BYTES | CPU, memory, swapping |
| Memory Statistics | Host Mem, SGA use, PGA use, % Host Mem used for SGA+PGA | Memory |
| IO Profile, IOStat by Function | Request/s, MB/s, per fungsi (LGWR, DBWR, Direct Reads) | Disk I/O |
| Wait Events (foreground dan background) | Event, avg wait (ms), % DB time, wait class | Latency I/O, redo, lock |
| SQL ordered by Elapsed Time, CPU Time, Gets, Reads, Executions, Parse Calls | SQL ID, SQL Module, elapsed, CPU, executions, per exec, %Total, %CPU, %IO; SQL text dari Complete List of SQL Text | G4 (klasifikasi SQL) |
| PGA dan advisory | PGA Aggr Summary (optimal, one-pass, multi-pass), Buffer Pool, SGA, dan Shared Pool Advisory | Sizing memory |
| Segments by ... | Logical reads, physical reads, row lock waits, buffer busy waits | Objek panas |
| Instance Activity Stats | Log switches, table scans (long tables), parse count, user commits | Redo, full scan |
| Section RAC | Global Cache Load Profile dan Efficiency, avg block receive time, DB time per instance | RAC |
| Modified Parameters | `sga_target`, `pga_aggregate_target`, `processes`, `cpu_count`, parameter optimizer | Konfigurasi |

**Section tambahan yang ditemukan di sample 19c** dan wajib diekstrak:

| Section AWR | Metrik | Dipakai untuk |
| --- | --- | --- |
| Top ADDM Findings (di Report Summary) | Nama finding, avg active sessions, % active sessions, jam mulai dan selesai | Bukti pembanding dan penentuan jam puncak |
| Operating System Statistics - Detail | Load, %busy, %iowait per snapshot | Profil per jam pada AWR periode panjang |
| Service Statistics | DB time dan DB CPU per service | Sinyal klasifikasi; porsi beban lewat `SYS$USERS` |
| Instance Activity Stats - Thread Activity | `log switches (derived)` total dan per jam | Redo |
| SQL ordered by User I/O Wait Time | SQL ID, module, waktu User I/O | Mengaitkan temuan I/O ke SQL |

**Validasi nilai.** Rasio di luar rentang wajar ditandai `invalid` dan tidak dipakai untuk temuan. Contoh nyata dari sample: Library Hit % = -433,41.

**Kontrak `awr.json`.** Nilai di bawah hanya contoh bentuk datanya.

```json
{
  "meta": {"db_name": "PRODDB", "rac": true, "cpus": 32, "elapsed_min": 60.0, "db_time_min": 245.3},
  "derived": {"aas": 4.09, "aas_per_cpu": 0.128, "db_cpu_pct_dbtime": 41.2},
  "top_events": [{"event": "db file sequential read", "pct_db_time": 38.4, "avg_wait_ms": 9.7, "wait_class": "User I/O"}],
  "sql": [{"sql_id": "abcd1234efgh5", "module": "JDBC Thin Client", "elapsed_s": 1840, "pct_total": 12.5, "category": "user_app", "confidence": "medium"}],
  "missing_sections": []
}
```

## 6. Status beban: sibuk atau tidak

Status beban ditentukan oleh dua rumus dan dua pembanding, bukan oleh satu angka CPU. Script menghitung semuanya dari `awr.json`.

```latex
AAS = \frac{\text{DB Time}}{\text{Elapsed Time}} \qquad\qquad \text{Load ratio} = \frac{AAS}{\text{jumlah CPU}}
```

```latex
\text{DB CPU share} = \frac{\text{DB CPU}}{\text{DB Time}} \times 100\%
```

Jumlah CPU diambil dari header (CPU logis). Bila jumlah core jauh lebih kecil dari CPU logis (hyperthreading), laporan mencatatnya sebagai catatan, tidak mengubah rumus.

**Status beban** **\[DEFAULT\]**

| Status | Load ratio | Arti |
| --- | --- | --- |
| Idle | < 0,10 | Beban sangat rendah. Temuan berbasis beban dibatasi maksimum P3. |
| Normal | 0,10 – 0,50 | Kapasitas longgar. |
| Sibuk | 0,50 – 1,00 | Mendekati kapasitas, perlu dilihat bersama wait dan CPU host. |
| Jenuh | 1,00 – 2,00 | Sesi mulai antre, baik untuk CPU maupun wait. |
| Kritis | > 2,00 | Antrean berat. Hampir pasti ada dampak ke user. |

Load ratio tinggi belum tentu CPU penuh, karena AAS juga menghitung sesi yang menunggu. Jenis bottleneck ditentukan oleh kombinasi dengan CPU:

| Load ratio | DB CPU share | CPU host (%Idle) | Kesimpulan |
| --- | --- | --- | --- |
| Tinggi | Tinggi (> 70%) | Rendah (< 10%) | **CPU-bound.** Cari SQL dengan CPU terbesar dan parse berlebih. |
| Tinggi | Rendah (< 40%) | Tinggi | **Wait-bound.** Wait class teratas menentukan arah analisa (I/O, lock, redo, dan seterusnya). |
| Rendah | Apa pun | Rendah (< 10%) | **Beban non-database di host.** Ada proses lain yang memakai CPU; laporan menyarankan cek di level OS. |
| Rendah | Apa pun | Tinggi | **Database tidak menjadi masalah** pada periode ini. Bila user tetap mengeluh, periksa periode snapshot atau lapisan aplikasi dan jaringan. |

Kalimat terakhir itu penting untuk DBA: laporan harus berani menyatakan "database sehat di periode ini", sehingga diskusi dengan user berpindah ke lapisan lain.

**AWR periode panjang (lebih dari 2 jam).** AWR harian 24 jam adalah pola pemakaian utama, terutama untuk perbandingan harian terhadap baseline, jadi skill melaporkan dua status: rata-rata periode dan puncak. Jam puncak diambil dari Top ADDM Findings dan Operating System Statistics - Detail. Severity berbasis beban memakai status puncak dan menyebut jamnya.

Contoh dari sample (24 jam, 34 CPU):

| Ukuran | Nilai | Status |
| --- | --- | --- |
| Rata-rata 24 jam | AAS 5,53; load ratio 0,16 | Normal |
| Puncak 22:00–23:00 (ADDM) | Avg active sessions 29,15; load ratio 0,86 | Sibuk |
| Snapshot 23:00 (OS Detail) | %busy 26,05; %iowait 11,42 | Pendukung puncak |

Tanpa aturan ini, laporan akan menyimpulkan "database santai", padahal ada satu jam yang hampir menyentuh kapasitas. Untuk jam puncak, skill menyarankan AWR per jam agar penyebabnya bisa dianalisa tanpa tercampur 23 jam lainnya.

## 7. Severity P0–P4

Severity ditentukan oleh aturan terukur di `thresholds.yaml`, bukan oleh penilaian bebas AI.

| Level | Arti | Kriteria contoh **\[DEFAULT\]** | Tindakan |
| --- | --- | --- | --- |
| P0 Kritis | Layanan terganggu atau hampir berhenti | Load ratio ≥ 2,0 dengan CPU host %Idle < 5; atau satu wait non-idle ≥ 30% DB time dengan latency di atas ambang kritis; atau `log file switch (checkpoint incomplete)` dominan | Tangani sekarang |
| P1 Tinggi | Dampak ke user jelas, belum berhenti | Load ratio 1,0–2,0; satu wait 15–30% DB time; swapping terdeteksi; satu SQL ≥ 40% DB time | Tangani hari ini |
| P2 Sedang | Degradasi terukur dan berisiko membesar | Satu wait 5–15% DB time; latency di atas ambang peringatan; Soft Parse % < 80; overhead monitoring ≥ 10% DB time | Jadwalkan minggu ini |
| P3 Rendah | Peluang optimasi | Satu SQL 10–20% DB time; log switch tinggi; overhead monitoring ≥ 5% DB time | Masuk backlog |
| P4 Info | Catatan konfigurasi atau observasi tanpa dampak | Parameter tidak standar; advisory menyarankan resize | Dicatat saja |

**Aturan penentuan**

1. Severity akhir sebuah temuan adalah level tertinggi dari semua kriteria yang terpenuhi.
2. Pembatas beban rendah: bila status beban Idle atau elapsed < 10 menit, temuan berbasis beban dibatasi maksimum P3 dan diberi label confidence rendah. Temuan konfigurasi tidak terpengaruh.
3. Event dengan wait class Idle tidak pernah menjadi temuan.
4. Temuan yang memakai kurang dari 1% DB time tidak dilaporkan, kecuali P0/P1 berbasis lost block atau error.
5. Pada RAC, severity dihitung per instance. Laporan menampilkan level tertinggi dan menyebut instance-nya.
6. Setiap temuan menyimpan `triggered_by` (ID aturan di `thresholds.yaml`) agar DBA bisa melihat mengapa severity itu muncul.

## 8. Klasifikasi SQL: monitoring atau user

Klasifikasi memakai bukti tidak langsung dan selalu berlabel confidence, karena section "SQL ordered by ..." pada AWR standar menampilkan SQL ID, SQL Module, dan SQL text, tetapi tidak menampilkan parsing schema. **Asumsi A1 terkonfirmasi pada sample 19c: kolomnya hanya SQL Id, SQL Module, dan SQL Text.** Parsing schema aktual baru terlihat di `DBA_HIST_SQLSTAT.PARSING_SCHEMA_NAME`, sehingga skill menyertakan SQL untuk memverifikasinya.

| Kategori | Arti | Sinyal khas |
| --- | --- | --- |
| `user_app` | SQL dari aplikasi atau user schema | Objek milik schema di `user_schemas`; nama modul aplikasi |
| `monitoring` | Pengumpul metrik dan health check | Akses `V$`, `GV$`, `DBA_*`, `X$`; modul OEM, Zabbix, Prometheus exporter, Nagios; query heartbeat |
| `maintenance` | Pekerjaan pemeliharaan terjadwal | `DBMS_STATS`, RMAN, Data Pump, auto task (`DBMS_SCHEDULER`) |
| `oracle_internal` | SQL rekursif dan internal | Modul `MMON_SLAVE`, `MMNL`; SQL rekursif pada dictionary |
| `unknown` | Bukti tidak cukup | Tidak dipaksakan menjadi `user_app` |

**Aturan, dievaluasi berurutan; aturan pertama yang cocok menang**

1. Daftar eksplisit dari DBA: `monitoring_modules`, `monitoring_schemas`, `user_schemas`.
2. Pola modul bawaan di `references/sql_classification.yaml`, misalnya OEM atau emagent untuk monitoring, `MMON_SLAVE` untuk internal, RMAN dan Data Pump untuk maintenance.
3. Pola SQL text: hanya mengakses objek dictionary atau fixed view berarti monitoring; `DBMS_STATS` berarti maintenance; `SELECT 1 FROM DUAL` berarti heartbeat dengan confidence rendah, karena connection pool aplikasi juga memakainya.
4. Pemilik objek: nama `OWNER.TABLE` pada SQL text yang cocok dengan `user_schemas` berarti `user_app`.
5. Tidak ada yang cocok: `unknown`.

Modul generik seperti `JDBC Thin Client`, `SQL*Plus`, atau `python` tidak cukup sebagai satu-satunya bukti, karena dipakai aplikasi maupun script monitoring. Sinyal generik hanya menambah confidence bila searah dengan sinyal lain.

**Format SQL Module dari sample.** Bila aplikasi tidak mengeset module, Oracle mengisinya dengan `PROGRAM@host (TNS V1-V3)`, misalnya `APPSRV@<host aplikasi>`. Parser memecahnya menjadi program dan host client, lalu memakai dua sinyal tambahan:

- Host client sama dengan host database dan programnya bukan proses Oracle: kemungkinan agent monitoring lokal (confidence medium).
- Host client ada di daftar `app_hosts`, atau program ada di `app_programs`: `user_app`.

SQL aplikasi di sample hampir selalu tanpa prefix owner (`INSERT INTO APP_CALL_EVENTS ...`), sehingga aturan 4 jarang berlaku. Karena itu konfigurasi ditambah `app_table_prefixes` (misalnya `APP_`, `ORD_`). Nama service dari Service Statistics juga menjadi sinyal; pada sample, 44% DB time masuk lewat `SYS$USERS`.

**Kategori tambahan `adhoc`.** Query interaktif dari tool seperti SQL Developer, Toad, PL/SQL Developer, atau SQL\*Plus di laptop user dipisahkan dari `user_app`. Pada sample, SQL Developer menjalankan `select * from APP_SMS_EVENTS where duration > 0`; DBA perlu tahu query seperti ini terpisah dari beban aplikasi.

**Kasus kalibrasi dari sample** (menjadi test case A5):

| SQL ID | SQL Module | Bukti | Kategori benar |
| --- | --- | --- | --- |
| mon000000001a | `dbspicao11@<host DB>` | `DBA_DATA_FILES` + `SYS.FILEXT$`; host client = host DB | `monitoring` (high) |
| mnt000000001a | `DBMS_SCHEDULER` | Komentar `/* SQL Analyze(1) */` dari DBMS\_STATS | `maintenance` (high) |
| hbt000000001a | `Poller` | `SELECT 1 FROM DUAL` | `monitoring` heartbeat (low) |
| app000000002b | `Poller` | `SELECT ... FROM LCK_SEMAPHORE ... FOR UPDATE` (tabel aplikasi) | `user_app` (medium) |
| adh000000001a | `SQL Developer` | Query ke tabel aplikasi | `adhoc` (high) |
| app000000001a | `APPSRV@<host aplikasi>` | `INSERT INTO APP_CALL_EVENTS` | `user_app` (high bila host terdaftar) |
| int000000001a | (kosong) | `INSERT INTO wri$_adv_objects` | `oracle_internal` (high) |
| mon000000005e | (kosong) | `WITH MONITOR_DATA ... GV$(CURSOR(...))` | `monitoring` (medium) |

Dua baris `Poller` menunjukkan bahwa satu nama modul bisa menghasilkan kategori berbeda. Klasifikasi selalu dinilai per SQL, tidak per modul.

**Mengapa ini penting: angka dari sample.** SQL monitoring `dbspicao11` adalah SQL nomor 1 berdasarkan elapsed time (9,19% DB time; 16,26 detik per eksekusi; 2.701 eksekusi, kira-kira setiap 32 detik). Empat SQL monitoring di top 15 total sekitar 13,8% DB time, sehingga memicu temuan "Overhead monitoring" P2.

**Confidence.** *High*: cocok dengan daftar eksplisit DBA atau dua sinyal atau lebih yang searah. *Medium*: satu sinyal kuat. *Low*: satu sinyal lemah.

**Keluaran per SQL:** `category`, `confidence`, `reasons[]` (sinyal yang dipakai), dan referensi ke SQL verifikasi `DBA_HIST_SQLSTAT`.

**Pelaporan.** Top SQL ditampilkan terpisah per kategori. Bagian "Overhead monitoring" menjumlahkan % DB time dan CPU dari kategori `monitoring` dan `maintenance`: ≥ 5% menjadi temuan P3, ≥ 10% menjadi P2, dengan saran mengurangi frekuensi atau memindahkan jadwalnya.

## 9. Analisa per domain

Setiap domain punya sinyal AWR, ambang batas, dan severity default. Angka di bawah adalah titik awal dari praktik umum, bukan standar Oracle, dan harus disesuaikan dengan baseline environment kamu di `thresholds.yaml`.

| Domain | Sinyal di AWR | Ambang batas default | Severity |
| --- | --- | --- | --- |
| CPU | Load ratio; Host CPU %Idle; DB CPU share; % DB time waiting for CPU (Resource Manager); `resmgr:cpu quantum` | Load ratio ≥ 1,0 dan %Idle < 10; ≥ 2,0 dan %Idle < 5; `resmgr:cpu quantum` ≥ 5% DB time | P1; P0; P2 |
| Memory | % Host Mem used for SGA+PGA; `VM_IN_BYTES`/`VM_OUT_BYTES`; PGA multi-pass; `direct path read/write temp` | `VM_OUT_BYTES` > 0 terus-menerus; SGA+PGA > 90% memori host; multi-pass > 0 | P1; P2; P3 |
| Disk I/O | Avg `db file sequential read`, `db file scattered read`, `direct path read`; IO MB/s dan request/s; `free buffer waits` | Single-block: hdd/san 10/20 ms, flash 3/8 ms. Multi-block: hdd/san 20/40 ms, flash 10/20 ms (lihat kalibrasi). | P2 (peringatan); P1 (kritis) |
| Redo dan commit | Avg `log file sync`, avg `log file parallel write`, log switch per jam, `log file switch (checkpoint incomplete)` | `log file sync` ≥ 10 ms atau ≥ 20 ms; switch > 6/jam atau > 20/jam per thread; `checkpoint incomplete` ≥ 1% DB time | P2 atau P1; P3 atau P2; P1 |
| Parsing | Soft Parse %; hard parse/s; Library Hit %; `library cache: mutex X`; `cursor: pin S wait on X`; parse time elapsed | Soft Parse % < 90 atau < 80; hard parse ≥ 100/s; mutex dan cursor wait ≥ 5% DB time | P3 atau P2; P2; P1 |
| Concurrency | `enq: TX - row lock contention`, `enq: TM`, `buffer busy waits`, `latch: cache buffers chains`, `library cache lock` | Gabungan ≥ 5%, ≥ 15%, ≥ 30% DB time | P2, P1, P0 |
| Network dan client | `SQL*Net more data to client`, `SQL*Net message to client` (event idle `SQL*Net message from client` diabaikan) | ≥ 10% DB time | P3 (cek fetch size dan jaringan) |
| RAC | Avg `gc cr/current block` receive time; `gc buffer busy`; lost block; DB time per instance | Avg ≥ 5 ms atau ≥ 10 ms; lost block > 0; rasio DB time antar instance > 3:1 | P2 atau P1; P1; P3 |
| SQL tunggal | % DB time satu SQL (per kategori, bagian 8) | ≥ 10%, ≥ 20%, ≥ 40% | P3, P2, P1 |

**Aturan umum**

- Ambang batas latency per event hanya dipakai bila event itu memakai minimal 1% DB time, supaya angka besar pada event kecil tidak jadi noise.
- Kombinasi menaikkan prioritas: temuan SQL naik satu level bila SQL itu berada di event wait yang sedang melewati ambang kritis.
- Hasil buffer hit ratio tidak pernah berdiri sendiri sebagai temuan; ia hanya konteks tambahan.
- Tiap temuan memuat rekomendasi tindakan dan risikonya (misalnya perlu downtime, perlu change approval), dan tidak berisi perintah yang bisa dieksekusi tanpa review.

**Kalibrasi dari sample**

1. **Pisahkan ambang single-block dan multi-block.** Read multi-block wajar lebih lambat. Default baru: single-block (`db file sequential read`) 3/8 ms untuk flash dan 10/20 ms untuk hdd/san; multi-block (`db file scattered read`, `direct path read`) 10/20 ms untuk flash dan 20/40 ms untuk hdd/san. Di sample, `db file sequential read` 0,49 ms (sehat) sedangkan `direct path read` 15,08 ms dengan 20,3% DB time.
2. **% DB time tinggi dengan latency sehat adalah masalah volume, bukan storage.** `db file sequential read` di sample memakan 32,1% DB time dengan latency 0,49 ms. Temuan seperti ini diarahkan ke SQL ordered by User I/O Wait Time dan Segments by Physical Reads, dan severity-nya dibatasi P2.
3. **`control file sequential read` di foreground ≥ 5% DB time menjadi temuan P3.** Di sample nilainya 6,7%. Dugaan penyebabnya adalah query monitoring ke `DBA_DATA_FILES` dan `v$filestat` yang berjalan setiap sekitar 32 detik; verifikasi dengan V-01 untuk SQL ID tersebut.
4. **Execute to Parse % < 50 dengan Soft Parse % > 95 menjadi temuan P3:** aplikasi mem-parse ulang hampir setiap eksekusi. Di sample: Execute to Parse 4,28%, Soft Parse 99,98%.
5. **Log switch.** Sample menunjukkan 7,79 switch per jam (rata-rata 24 jam), yang memicu P3 pada ambang default > 6/jam. Pada AWR periode panjang, nilai per jam dari Thread Activity tetap rata-rata, sehingga verifikasi per jam memakai V-03.

## 10. Format laporan

Setiap run menghasilkan folder `awr-report-<db>-<inst>-<snapbegin>_<snapend>/` berisi `report.md`, `report.html`, `findings.json`, dan `awr.json`. Laporan dibuat dalam dua lapisan: ringkasan untuk user non-teknis di bagian atas, detail teknis untuk DBA di bawahnya.

**Urutan isi laporan**

1. Ringkasan eksekutif (maksimal 5 baris, bahasa awam): status database, jumlah temuan per severity, dan tindakan yang paling mendesak.
2. Informasi database dan periode (ringkasan header dari bagian 3).
3. Kesehatan beban: load ratio, DB CPU share, dan kesimpulan bottleneck (bagian 6).
4. Daftar temuan, diurutkan P0 ke P4.
5. Top SQL per kategori: user, monitoring, maintenance, internal, unknown (bagian 8).
6. Detail per domain (bagian 9).
7. Lampiran SQL verifikasi.
8. Saran AWR diff (bagian 12).
9. Lampiran: metrik mentah, asumsi, section AWR yang tidak ditemukan, dan batasan analisa.

**Template satu temuan**

```markdown
### [P1] High disk read latency on db file sequential read
- Domain: Disk I/O | Instance: 1 | Confidence: high
- Evidence: avg wait 21.4 ms; 38.4% of DB time (Top Foreground Events)
- Facts: ...
- Hypothesis: ... (clearly labeled when there is no direct evidence)
- User impact: ...
- How to verify: see SQL V-03, V-07 in the appendix
- Recommendation: ... | Change risk: ... | Change approval required: yes/no
- Triggered by: io.seq_read.crit
```

**Visual wajib di `report.html`**

| Visual | Bentuk | Menjawab |
| --- | --- | --- |
| Komposisi DB time | Bar atau donut: DB CPU dan wait class | Waktu database habis di mana |
| Top 5 wait event | Bar horizontal, % DB time, berwarna menurut severity | Bottleneck terbesar |
| Load ratio | Meter atau bar terhadap garis 1,0 | Sibuk atau tidak |
| Top SQL | Bar horizontal, diwarnai menurut kategori SQL | Siapa pemakai terbesar: user atau monitoring |
| Latency I/O | Bar per event terhadap garis ambang | Disk lambat atau tidak |
| Perbandingan instance (RAC) | Bar berkelompok per instance | Beban seimbang atau tidak |

**Persyaratan HTML.** File tunggal dan self-contained, tanpa CDN atau request eksternal, supaya bisa dibuka offline dan dikirim ke user. Chart berupa SVG inline. Warna tidak menjadi satu-satunya pembawa makna: severity juga ditulis sebagai teks. Bahasa laporan default Inggris dan bisa diganti lewat parameter `lang`; nama event, SQL, dan istilah Oracle tetap dalam bahasa Inggris.

## 11. SQL verifikasi

Setiap temuan membawa SQL read-only dari katalog di `references/verify_sql/`, dan skill tidak menulis SQL baru dari ingatan. Katalog memakai satu file per domain, dan tiap query punya header metadata yang dibaca oleh linter.

```sql
-- id: V-01
-- title: Parsing schema aktual untuk satu SQL ID
-- domain: sql_classification
-- min_version: 11.2
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap, sql_id
SELECT s.sql_id, s.parsing_schema_name, s.module, s.action,
       SUM(s.executions_delta)                 AS execs,
       ROUND(SUM(s.elapsed_time_delta)/1e6, 1) AS elapsed_s,
       ROUND(SUM(s.cpu_time_delta)/1e6, 1)     AS cpu_s
FROM   dba_hist_sqlstat s
WHERE  s.dbid = &dbid
AND    s.instance_number = &inst_id
AND    s.snap_id BETWEEN &begin_snap + 1 AND &end_snap
AND    s.sql_id = '&sql_id'
GROUP  BY s.sql_id, s.parsing_schema_name, s.module, s.action;
```

Delta di `DBA_HIST_SQLSTAT` pada snapshot N mencakup interval antara snapshot N-1 dan N, sehingga rentang yang benar adalah `begin_snap + 1` sampai `end_snap`. Dua contoh lain yang wajib ada di katalog:

```sql
-- id: V-02
-- domain: sql_classification
-- min_version: 12.1
-- license: diagnostics_pack
-- params: dbid, inst_id, begin_snap, end_snap
-- title: Beban per parsing schema (untuk memastikan klasifikasi user vs monitoring)
SELECT s.parsing_schema_name,
       ROUND(SUM(s.elapsed_time_delta)/1e6) AS elapsed_s,
       ROUND(SUM(s.cpu_time_delta)/1e6)     AS cpu_s,
       SUM(s.executions_delta)              AS execs
FROM   dba_hist_sqlstat s
WHERE  s.dbid = &dbid AND s.instance_number = &inst_id
AND    s.snap_id BETWEEN &begin_snap + 1 AND &end_snap
GROUP  BY s.parsing_schema_name
ORDER  BY elapsed_s DESC
FETCH FIRST 10 ROWS ONLY;

-- id: V-03
-- domain: redo
-- min_version: 11.2
-- license: none
-- params: (tidak ada)
-- title: Frekuensi log switch per jam per thread
SELECT TO_CHAR(first_time, 'YYYY-MM-DD HH24') AS jam, thread#, COUNT(*) AS switches
FROM   v$log_history
WHERE  first_time > SYSDATE - 1
GROUP  BY TO_CHAR(first_time, 'YYYY-MM-DD HH24'), thread#
ORDER  BY 1, 2;
```

**Aturan wajib**

1. Hanya `SELECT` atau `WITH`. Tidak ada DML, DDL, `ALTER SYSTEM`, `KILL SESSION`, atau `EXECUTE`. Package yang boleh hanya `DBMS_XPLAN.DISPLAY_AWR`, `DBMS_XPLAN.DISPLAY_CURSOR`, dan `DBMS_WORKLOAD_REPOSITORY.AWR_*_REPORT_*`.
2. Parameter memakai substitution variable (`&dbid`, `&inst_id`, `&begin_snap`, `&end_snap`, `&sql_id`) dan nilainya diisi dari `awr.json`.
3. Setiap query diberi tag lisensi, dan laporan menampilkannya di samping query.
4. Query yang memakai sintaks di atas versi Oracle target ditandai `min_version`, dan skill memilih varian yang sesuai atau menandai tidak tersedia.
5. `lint_verify_sql.py` menolak query yang melanggar aturan di atas sebelum laporan dibuat.
6. Perintah perubahan (ubah parameter, buat index, resize redo) hanya muncul sebagai teks rekomendasi dengan tanda "perlu change approval", tidak sebagai blok SQL siap salin.

| Tag lisensi | Cakupan |
| --- | --- |
| `none` | View `V$` dinamis seperti `v$log_history` dan `v$sql` |
| `diagnostics_pack` | `DBA_HIST_*`, `V$ACTIVE_SESSION_HISTORY`, paket AWR dan ADDM |
| `tuning_pack` | `DBMS_SQLTUNE`, SQL Tuning Advisor, SQL Monitor |

## 12. Saran AWR diff

Skill menyarankan AWR diff hanya bila ada pembanding yang masuk akal, lalu menyiapkan script yang siap dijalankan DBA. Satu AWR tidak bisa menunjukkan apa yang "normal" bagi database itu, dan di situlah diff berguna.

**Kapan disarankan**

- User menyebut pembanding waktu, misalnya "kemarin masih normal", "sejak deploy", atau "minggu lalu cepat".
- Ada temuan P0 atau P1 yang tidak punya penjelasan konfigurasi yang jelas.
- Database berstatus jenuh atau kritis, tetapi workload (executes/s, transaksi/s) tampak biasa.

**Isi saran**

1. Alasan singkat mengapa diff membantu pada kasus ini.
2. Snap ID periode sekarang, diambil dari header AWR.
3. Kriteria baseline yang baik: jam dan hari kerja yang sama pada minggu sebelumnya, durasi serupa, tanpa instance restart di antaranya. Skill meminta DBA menyebut snap ID baseline.
4. File `awr_diff_<db>.sql` yang siap jalan. Kerangkanya di bawah; signature fungsi harus mengikuti dokumentasi Oracle untuk versi target dan dicatat di `references/awr_diff.md`, tidak diambil dari ingatan AI.

```sql
SET LONG 100000000 LONGCHUNKSIZE 100000 PAGESIZE 0 LINESIZE 32767 TRIMSPOOL ON FEEDBACK OFF
SPOOL awr_diff_PRODDB.html
SELECT output
FROM   TABLE(DBMS_WORKLOAD_REPOSITORY.AWR_DIFF_REPORT_HTML(
         &dbid, &inst_id, &base_begin_snap, &base_end_snap,
         &dbid, &inst_id, &cur_begin_snap,  &cur_end_snap));
SPOOL OFF
```

Untuk RAC, skill memakai varian global (`AWR_GLOBAL_DIFF_REPORT_HTML`). Alternatif interaktif adalah `@?/rdbms/admin/awrddrpt.sql`.

**Peringatan perbandingan.** Skill memberi peringatan bila durasi kedua periode berbeda lebih dari 20%, atau ada restart di antaranya, atau jumlah CPU berbeda. Perbandingan seperti itu tetap bisa dijalankan, tetapi kesimpulannya diberi label confidence rendah.

**v1.1.** Skill menerima dua file AWR dan membuat tabel delta (metrik utama, top event, top SQL yang naik atau turun) dari dua `awr.json`, tanpa menunggu diff dijalankan di database.

## 13. Struktur skill

Skill dibuat dalam format Claude Skills, dengan `SKILL.md` yang ringkas dan detail yang dimuat bertahap dari `references/`. Script bersifat netral terhadap agent, sehingga agent AI lain bisa memakainya lewat file instruksi sendiri.

```text
oracle-awr-analyzer/
├── SKILL.md                    # alur kerja dan aturan, maksimal 500 baris
├── scripts/
│   ├── parse_awr.py            # AWR HTML/text -> awr.json
│   ├── analyze.py              # awr.json + thresholds -> findings.json
│   ├── classify_sql.py         # kategori dan confidence per SQL
│   ├── validate_findings.py    # cek silang angka temuan vs awr.json
│   ├── lint_verify_sql.py      # tolak SQL verifikasi yang tidak read-only
│   ├── render_report.py        # findings.json -> report.md + report.html
│   ├── awr_diff_sql.py         # buat awr_diff_<db>.sql
│   └── tests/
├── references/
│   ├── thresholds.yaml         # semua ambang batas dan aturan severity
│   ├── sql_classification.yaml # pola modul, teks, dan schema
│   ├── wait_events.md          # event -> domain -> arti -> penyebab umum -> cara verifikasi
│   ├── awr_diff.md             # signature fungsi diff per versi
│   ├── report_template.md
│   └── verify_sql/             # satu file .sql per domain
├── assets/
│   └── report.html.tpl         # template HTML self-contained
└── examples/                   # AWR sample yang sudah disanitasi + keluaran yang diharapkan
```

**Draft `SKILL.md`**

```markdown
---
name: oracle-awr-analyzer
description: Analisa laporan Oracle AWR (HTML atau text), single instance maupun RAC, lalu hasilkan laporan temuan berseverity P0-P4 dalam Markdown dan HTML visual. Gunakan saat user melampirkan file AWR, menyebut AWR/awrrpt/awrgrpt, atau melaporkan database Oracle lambat dan meminta analisa performa.
---

# Oracle AWR Analyzer

Alur kerja:
1. Validasi file: harus laporan AWR. Jika bukan, berhenti dan jelaskan.
2. Jalankan scripts/parse_awr.py -> awr.json. Jangan membaca HTML mentah.
3. Jalankan scripts/analyze.py dan scripts/classify_sql.py -> findings.json.
4. Tafsirkan: tulis dugaan penyebab, dampak ke user, dan rekomendasi. Tandai dugaan sebagai Dugaan.
5. Jalankan scripts/validate_findings.py dan scripts/lint_verify_sql.py. Perbaiki sampai lolos.
6. Jalankan scripts/render_report.py -> report.md dan report.html.
7. Sarankan AWR diff sesuai references/awr_diff.md bila kriterianya terpenuhi.

Aturan keras:
- Semua angka berasal dari awr.json. Jangan menghitung atau mengarang angka sendiri.
- Jangan menghubungi database. Jangan menjalankan SQL.
- SQL verifikasi hanya dari references/verify_sql/.
- Bila section tidak ada, katakan tidak ada. Jangan menebak.
```

**Dependensi** **\[DEFAULT\]**: Python 3, `beautifulsoup4`, `lxml`, `pyyaml`. Chart dibuat sebagai SVG oleh script sendiri, tanpa matplotlib dan tanpa CDN, supaya `report.html` tetap satu file. Bila library tidak tersedia, script jatuh ke `html.parser` bawaan Python dan mencatat penurunan akurasi.

## 14. Keamanan, privasi, dan edge case

Skill bekerja offline terhadap satu file dan tidak mengubah apa pun di database, sehingga risiko utamanya ada pada isi laporan yang dibagikan ke user.

**Privasi dan lisensi**

- SQL text di AWR bisa memuat literal (nama, nomor, email) dan nama host. Bagian laporan untuk `audience=user` memasking literal string dan angka, sedangkan SQL lengkap hanya ada di bagian DBA dan di `findings.json`.
- Skill tidak mengirim data ke layanan eksternal dan `report.html` tidak memuat request eksternal.
- AWR dan `DBA_HIST_*` membutuhkan Oracle Diagnostics Pack. Footer laporan menyebut ini dan menandai lisensi tiap SQL verifikasi.
- Skill tidak pernah mengubah file input; semua keluaran ditulis ke folder baru.

**Edge case dan perilaku yang diharapkan**

| Kasus | Perilaku skill |
| --- | --- |
| File bukan AWR, atau terpotong | Berhenti bila bukan AWR. Bila terpotong, analisa section yang ada dan daftar yang hilang di lampiran. |
| Section tertentu tidak ada (versi lama, fitur nonaktif) | Nilai `null` dengan alasan. Tidak ada angka karangan. |
| Instance restart di antara dua snapshot | Peringatan di bagian atas; confidence turun ke rendah. |
| Elapsed kurang dari 10 menit | Peringatan; temuan berbasis beban dibatasi P3. |
| Elapsed lebih dari 2 jam, termasuk AWR harian 24 jam | Laporkan status rata-rata dan status puncak (bagian 6). Jam puncak diambil dari Top ADDM Findings dan OS Statistics - Detail; sarankan AWR per jam untuk jam puncak. |
| Database idle | Laporan menyatakan "tidak ada masalah performa pada periode ini". Tidak memaksakan temuan. |
| RAC dengan beban tidak seimbang | Severity dihitung per instance; laporan membandingkan antar instance. |
| Report global `awrgrpt` | Dipecah per instance bila datanya tersedia; sisanya dianalisa sebagai agregat. |
| CDB atau PDB | Ditandai di header; analisa PDB-level di luar v1 dan diberi catatan. |
| Angka berformat lokal (1.234,5 atau 1,234.5) | Parser menormalkan sebelum disimpan ke `awr.json`. |
| File sangat besar | Seluruh parsing dilakukan script dan LLM tidak memuat file mentah ke konteks. |
| Beberapa file dalam satu permintaan | v1 memproses satu per satu dan menyebutnya. Mode batch masuk v1.1. |

## 15. Kriteria penerimaan, roadmap, dan asumsi

Skill dianggap selesai bila sepuluh kriteria berikut lolos pada seluruh AWR sample.

| ID | Kriteria | Cara uji | Goal |
| --- | --- | --- | --- |
| A1 | Header benar 100% (DB name, DBID, instance, RAC, CPU, memori, snap ID, waktu, elapsed, DB time) | Dibandingkan dengan nilai yang dicatat DBA | G1 |
| A2 | Semua angka di `findings.json` sama persis dengan `awr.json` (toleransi nol) | `validate_findings.py` | G3, G5 |
| A3 | Status beban sesuai penilaian DBA pada semua sample | Review DBA | G2 |
| A4 | Setiap temuan punya severity, bukti (metrik, nilai, section), confidence, minimal satu SQL verifikasi, rekomendasi, dan aturan pemicu | Validasi skema JSON | G3, G6 |
| A5 | Klasifikasi SQL cocok minimal 90% dengan label DBA pada minimal 50 SQL; semuanya punya alasan dan confidence | Pelabelan manual | G4 |
| A6 | Tidak ada klaim penyebab tanpa bukti yang tidak berlabel "Dugaan" | Review sample | Prinsip 3 |
| A7 | Semua SQL verifikasi lolos lint: read-only, tag lisensi, versi | `lint_verify_sql.py` | G6 |
| A8 | `report.md` dan `report.html` terbentuk; HTML terbuka offline tanpa request eksternal; enam visual tampil | Cek manual dan pencarian URL eksternal | G7 |
| A9 | AWR 20 MB selesai diparse dan dilaporkan dalam 60 detik (target, bisa disesuaikan) | Benchmark | - |
| A10 | Saran AWR diff muncul pada kasus yang memenuhi kriteria dan tidak muncul pada database idle tanpa pembanding | Tes kasus | G8 |

**Set sample uji.** Delapan AWR yang sudah disanitasi, masing-masing dengan keluaran yang diharapkan di `examples/`: idle; CPU-bound; I/O-bound; commit-heavy (`log file sync`); parse-heavy; lock contention; RAC dua node tidak seimbang; file terpotong. Sample pertama sudah ada: AWR harian 24 jam PRODDB (19c, single instance, non-CDB).

**Roadmap**

| Versi | Cakupan |
| --- | --- |
| v1 | G1–G8 untuk satu file AWR, single instance dan RAC |
| v1.1 | Mode batch beberapa file dengan ringkasan lintas database; tabel delta dari dua AWR; baseline per database |
| v2 | Tren dari riwayat run; ambang batas per database; analisa AWR level PDB dan Exadata |

**Asumsi yang perlu kamu konfirmasi**

- [x] A1: AWR tidak menampilkan parsing schema pada section SQL. Terkonfirmasi pada sample 19c.
- [x] A2: Versi Oracle yang dianalisa adalah 12.1 ke atas. Sample: 19.0.0.0.0.
- [x] A3: File AWR tersedia dalam format HTML. Sample: HTML dari awrrpt.
- [x] A4: Laporan default berbahasa Inggris (`lang=en`); `lang=id` tetap tersedia sebagai opsi. Dikonfirmasi DBA.
- [ ] A5: Lisensi Diagnostics Pack tersedia di semua database yang dianalisa.
- [ ] A6: Daftar host aplikasi, program aplikasi (`APPSRV`, `APPWORKER`, `APPBATCH`, ...), prefix tabel aplikasi, dan nama tool monitoring bisa kamu sediakan untuk `sql_classification.yaml`.
- [ ] A7: Profil storage tiap environment diketahui (hdd, san, atau flash). Sample: single-block read 0,49 ms, mengarah ke flash atau SSD.
- [ ] A8: `dbspicao11` adalah poller monitoring (dugaan: Smart Plug-in Oracle dari HP/Micro Focus Operations Agent), sedangkan `Poller` adalah komponen aplikasi.
- [ ] A9: `ReportPortal.exe` masuk `user_app` (reporting) atau `adhoc`.
