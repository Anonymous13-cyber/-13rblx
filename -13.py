# main.py
# Untuk menjalankan: uvicorn main:app --reload

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import requests
import logging

# --- Konfigurasi Logging ---
# Berguna untuk debugging saat aplikasi berjalan di server
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Inisialisasi Aplikasi FastAPI ---
app = FastAPI(
    title="Roblox Parental Request API",
    description="API untuk mengirim permintaan orang tua pada akun Roblox U13.",
    version="1.0.0"
)

# --- Middleware CORS ---
# Mengizinkan frontend (dari domain manapun) untuk berkomunikasi dengan backend ini
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Produksi: Ganti dengan domain frontend Anda, e.g., ["https://your-frontend.com"]
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# --- Model Data (Pydantic) ---
# Memastikan data yang masuk dari frontend memiliki format yang benar
class ParentRequest(BaseModel):
    cookie: str
    email: EmailStr # Validasi email secara otomatis

# --- Konstanta ---
MIN_COOKIE_LENGTH = 500 # Perkiraan panjang minimal cookie .ROBLOSECURITY
ROBLOX_LOGOUT_URL = "https://auth.roblox.com/v2/logout"
ROBLOX_API_URL = "https://apis.roblox.com/child-requests-api/v1/send-request-to-new-parent"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# --- Fungsi Helper ---

def validate_cookie(cookie: str):
    """
    Memvalidasi panjang cookie.
    Jika tidak valid, akan memunculkan HTTPException.
    """
    if len(cookie) < MIN_COOKIE_LENGTH:
        logger.warning(f"Validasi gagal: Cookie terlalu pendek ({len(cookie)} karakter).")
        raise HTTPException(
            status_code=400, 
            detail="Format cookie tidak valid atau terlalu pendek."
        )

def get_csrf_token(session: requests.Session) -> str:
    """
    Mengambil X-CSRF-TOKEN dari endpoint logout Roblox menggunakan session.
    Session digunakan untuk menjaga konsistensi cookie.
    """
    try:
        response = session.post(ROBLOX_LOGOUT_URL)
        response.raise_for_status()  # Akan error jika status code 4xx atau 5xx
        
        csrf_token = response.headers.get("x-csrf-token")
        if not csrf_token:
            logger.error("Gagal mengambil CSRF token: Header x-csrf-token tidak ditemukan.")
            raise HTTPException(status_code=500, detail="Gagal mengambil CSRF token dari Roblox.")
            
        logger.info("Berhasil mendapatkan CSRF token.")
        return csrf_token
    except requests.exceptions.RequestException as e:
        logger.error(f"Error saat menghubungi Roblox untuk CSRF token: {e}")
        raise HTTPException(status_code=502, detail="Tidak dapat terhubung ke server otentikasi Roblox.")


def send_request_to_roblox(session: requests.Session, csrf_token: str, parent_email: str) -> dict:
    """
    Mengirim permintaan final ke API Roblox.
    """
    headers = {
        "X-CSRF-TOKEN": csrf_token,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT
    }
    payload = {
        "email": parent_email,
        "requestDetails": {}
    }
    
    try:
        response = session.post(ROBLOX_API_URL, headers=headers, json=payload)
        
        # Roblox API bisa mengembalikan 200 OK bahkan jika ada error di dalam body
        # Jadi kita perlu cek respons JSON-nya
        response_data = response.json()
        logger.info(f"Respons dari Roblox API: {response_data}")

        if response.status_code == 200 and response_data.get('status') == 'SUCCESS':
             return {"status": "sukses", "message": "Permintaan berhasil dikirim.", "data": response_data}
        else:
            # Mengambil pesan error dari respons Roblox jika ada
            error_message = response_data.get('errors', [{}])[0].get('message', 'Terjadi kesalahan yang tidak diketahui.')
            return {"status": "gagal", "message": error_message, "data": response_data}

    except requests.exceptions.RequestException as e:
        logger.error(f"Error saat mengirim request ke Roblox API: {e}")
        raise HTTPException(status_code=502, detail="Tidak dapat terhubung ke API Roblox.")
    except ValueError: # JSONDecodeError
        logger.error(f"Gagal mem-parsing JSON dari Roblox. Respons: {response.text}")
        raise HTTPException(status_code=500, detail="Respons tidak valid dari server Roblox.")


# --- Endpoint Utama ---

@app.post("/send-request", tags=["Parental Request"])
def handle_send_parent_request(data: ParentRequest):
    """
    Endpoint utama untuk menangani permintaan pengiriman email orang tua.
    
    Proses:
    1. Validasi panjang cookie.
    2. Buat session Requests.
    3. Ambil CSRF token.
    4. Kirim permintaan ke API Roblox.
    5. Kembalikan respons ke frontend.
    """
    try:
        # 1. Validasi input
        validate_cookie(data.cookie)
        
        # 2. Buat session untuk menjaga cookie tetap sama di setiap request
        with requests.Session() as session:
            session.cookies['.ROBLOSECURITY'] = data.cookie
            
            # 3. Ambil CSRF token
            csrf_token = get_csrf_token(session)
            
            # 4. Kirim request utama
            result = send_request_to_roblox(session, csrf_token, data.email)

        # 5. Kembalikan hasil
        if result["status"] == "sukses":
            return JSONResponse(status_code=200, content=result)
        else:
            # Gunakan status code 400 untuk error yang disebabkan oleh input pengguna (e.g., cookie salah)
            return JSONResponse(status_code=400, content=result)

    except HTTPException as e:
        # Tangkap error yang sudah kita definisikan (seperti validasi)
        return JSONResponse(status_code=e.status_code, content={"status": "gagal", "message": e.detail})
    except Exception as e:
        # Tangkap error tak terduga lainnya
        logger.critical(f"Terjadi error tak terduga: {e}", exc_info=True)
        return JSONResponse(
            status_code=500, 
            content={"status": "gagal", "message": "Terjadi kesalahan internal pada server."}
        )

@app.get("/", tags=["Root"])
def read_root():
    """Endpoint root untuk verifikasi bahwa API berjalan."""
    return {"status": "API Berjalan", "message": "Selamat datang di Roblox Parental Request API!"}

