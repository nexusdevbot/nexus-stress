# nexus-stress v2 — Stress/Load Testing Tool (Brutal Edition)

> Tools stress-test buatan sendiri: **zero-dependency** (murni Python stdlib), live metrics tiap detik, latensi p50/p95/p99, plus **guardrail whitelist** biar amah cuma bisa nembak infra milik sendiri.
>
> **HANYA untuk pengujian infra milik sendiri / yang punya izin.** Nembak pihak ketiga = ilegal (UU ITE & setara negara lain).

---

## ✨ Metode Tersedia (v2)

| Mode | Layer | Fungsi | Catatan |
|---|---|---|---|
| `http` | L7 | HTTP flood keep-alive (GET/POST/HEAD + body JSON) | throughput max dengan `--fast` |
| `slowloris` | L7 | Tarik-napas: pegang koneksi & teteskan header | jebol server thread-pool |
| `hold` | L7/L4 | Connection exhaustion (buka & pegang idle) | uji max koneksi backend |
| `tls` | L7 | TLS handshake flood (negosiasi TLS baru tiap request) | berat di CPU backend |
| `tcp` | L4 | TCP connection flood | connect→send→close |
| `udp` | L4 | UDP packet flood | rate (`--pps`) & ukuran (`--size`) jelas |
| `auto` | semua | Jalankan http→tcp→udp→post→hold→slowloris berurutan + laporan gabungan | paling gampang buat test manual |
| `resolve` | — | Cek host lolos whitelist atau tidak | |

## 🧱 Guardrail
- Target **WAJIB** ada di `/root/.nexus-stress.allow` (boleh IP, CIDR, atau domain).
- File itu otomatis dibuat saat run pertama — tinggal tambah infra milikmu di sana, satu per baris.
- Caps aman: max **500 threads**, max **600 detik**, max **100k pps** total (UDP).
- Preset `--brutal` = threads 500 + durasi 300 detik + tanpa rate-limit (tetap terkunci whitelist).

---

## 🔧 Instalasi di VPS (1 menit)

```bash
# clone repo
git clone https://github.com/nexusdevbot/nexus-stress.git
cd nexus-stress

# (opsional) pindah biar gampang dipanggil
cp nexus-stress.py /root/nexus-stress.py && chmod +x /root/nexus-stress.py

# cek jalan
python3 /root/nexus-stress.py --help
```

> Butuh Python 3.8+ (Ubuntu 22.04 default udah cukup). **Tidak perlu pip install apa-apa.**

## 🚀 Cara Pakai Manual di VPS

### 1. Daftarkan target-mu sendiri dulu
```bash
# file whitelist otomatis dibuat saat run pertama
echo "1.2.3.4" >> /root/.nexus-stress.allow        # IP milikmu
echo "myserver-kusendiri.com" >> /root/.nexus-stress.allow   # domain milikmu

# cek apakah target lolos
python3 /root/nexus-stress.py resolve 1.2.3.4
```

### 2. Test cepat (laporan metrik lengkap)
```bash
# L7 — GET flood, 100 thread, 8 detik
python3 /root/nexus-stress.py http http://1.2.3.4:8080/ --threads 100 --time 8

# L7 — POST flood dengan body JSON
python3 /root/nexus-stress.py http http://1.2.3.4/app --method POST --data '{"test":1}' --threads 100 --time 8

# L7 — Slowloris (pegang 100 koneksi server)
python3 /root/nexus-stress.py slowloris 1.2.3.4:8080 --threads 4 --socks 25 --time 30

# L7/L4 — Connection exhaustion
python3 /root/nexus-stress.py hold 1.2.3.4:8080 --threads 100 --time 20

# L7 — TLS handshake flood (butuh URL https://)
python3 /root/nexus-stress.py tls https://serverku.com/ --threads 20 --time 15

# L4 — TCP flood
python3 /root/nexus-stress.py tcp 1.2.3.4:8080 --threads 100 --time 10

# L4 — UDP flood (500 pps x 2 thread, paket 512B)
python3 /root/nexus-stress.py udp 1.2.3.4:8080 --threads 2 --pps 500 --size 512 --time 10
```

### 3. Mode AUTO (semua metode sekaligus, laporan gabungan)
```bash
python3 /root/nexus-stress.py auto http://1.2.3.4:8080/ --time 8
```

### 4. Preset --brutal (max threads, max durasi)
```bash
python3 /root/nexus-stress.py http http://127.0.0.1:8899/ --brutal --fast
```

## 📊 Contoh Output
```
[L7 http] POST http://127.0.0.1:8899/ | threads=100 dur=8s rps/thread=∞
  [  2s] ok=    5543 (  2512/s) err=    0 conn=  100
  ...
===== RINGKASAN (req) =====
  total : 21706  (ok=21457, err=249)  | koneksi aktif=335
  latensi ms : p50=2.1  p95=3.1  p99=4.9  avg=7.3
======================
```

## 🆚 Hasil Terverifikasi (VPS 2 vCPU, target localhost:8899)
| Metode | Throughput | Catatan |
|---|---|---|
| HTTP GET (20 thr) | **2.800+ req/s** | err=0, p50 2.1 ms |
| HTTP POST brutal (100 thr) | **2.700+ req/s** | 21.711 req / 8 dtk |
| TCP (30 thr) | **3.060 conn/s** | err=0 |
| Slowloris (4x25 sock) | 100 koneksi pegang | server bertahan |
| Hold (100 thr) | 100 koneksi idle | target sehat setelahnya |

---

## ⚖️ Legal
Repositori ini hanya untuk **capacity-planning, tuning, dan uji load terhadap infra milik sendiri**. Untuk apa pun di luar itu: jangan. Penyalahgunaan melanggar hukum.

## 📜 Lisensi
MIT — © nexusdevbot
