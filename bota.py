import os
import json
import logging
import io
import uuid
from datetime import datetime
from dotenv import load_dotenv
import difflib 

# --- Importy Bibliotek ---
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CallbackQueryHandler, CommandHandler

# --- 1. Konfiguracja Logowania ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. Ładowanie Kluczy API ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.critical("BŁĄD: Nie znaleziono tokenów (TELEGRAM_TOKEN lub GEMINI_API_KEY) w pliku .env")
    exit()

# --- 3. Konfiguracja Google ---
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
GOOGLE_TOKEN_FILE = 'token.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

GOOGLE_SHEET_NAME = 'Odbiory_Kolonia_Warszawska'
WORKSHEET_NAME = 'Arkusz1'        # Arkusz do ZAPISU
WORKSHEET_ARCHIVE_NAME = 'Archiwum' # Arkusz do ODCZYTU (Archiwum) - UPEWNIJ SIĘ ŻE ISTNIEJE!

G_DRIVE_MAIN_FOLDER_NAME = 'Lokale'
G_DRIVE_SZEREGI_FOLDER_NAME = 'Szeregi'

# --- 3b. Lista Firm Wykonawczych (Oficjalna) ---
LISTA_FIRM_WYKONAWCZYCH = [
    "ANETA NIEWIADOMSKA ANER",
    "DOMHOMEGROUP SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
    "KAMEX",
    "EKO DOM DEVELOPER SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
    "SIL GROUP IVAN STETSIUK",
    "Kateryna Filiuk",
    "VL-STAL Vladyslav Loshytskyi",
    "RDR REMONTY SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
    "EL-ROM Sylwia Romanowska",
    "Complex Bruk Mateusz Oleksak",
    "Usługi Budowlane Michał Piskorz",
    "PRIMA TYNK Janusz Pelc",
    "Dachy płaskie hydroizolacje Grzegorz Madej"
]

# --- 3c. Dane do Przycisków ---
DANE_SZEREGOW = {
    "Szereg 1": {"zakres": "49-54", "lokale": ["49/1", "49/2", "50/1", "50/2", "51/1", "51/2", "52/1", "52/2", "53/1", "53/2", "54/1", "54/2"]},
    "Szereg 2": {"zakres": "38-43", "lokale": ["38/1", "38/2", "39/1", "39/2", "40/1", "40/2", "41/1", "41/2", "42/1", "42/2", "43/1", "43/2"]},
    "Szereg 3": {"zakres": "55-62", "lokale": ["55/1", "55/2", "56/1", "56/2", "57/1", "57/2", "58/1", "58/2", "59/1", "59/2", "60/1", "60/2", "61/1", "61/2", "62/1", "62/2"]},
    "Szereg 4": {"zakres": "44-48", "lokale": ["44/1", "44/2", "45/1", "45/2", "46/1", "46/2", "47/1", "47/2", "48/1", "48/2"]},
    "Szereg 5": {"zakres": "63-77", "lokale": ["63/1", "63/2", "64/1", "64/2", "65/1", "65/2", "66/1", "66/2", "67/1", "67/2", "68/1", "68/2", "69/1", "69/2", "70/1", "70/2", "71/1", "71/2", "72/1", "72/2", "73/1", "73/2", "74/1", "74/2", "75/1", "75/2", "76/1", "76/2", "77/1", "77/2"]},
    "Szereg 6": {"zakres": "1-7", "lokale": ["1/1", "1/2", "2/1", "2/2", "3/1", "3/2", "4/1", "4/2", "5/1", "5/2", "6/1", "6/2", "7/1", "7/2"]},
    "Szereg 7": {"zakres": "8-14", "lokale": ["8/1", "8/2", "9/1", "9/2", "10/1", "10/2", "11/1", "11/2", "12/1", "12/2", "13/1", "13/2", "14/1", "14/2"]},
    "Szereg 8": {"zakres": "32-37", "lokale": ["32/1", "32/2", "33/1", "33/2", "34/1", "34/2", "35/1", "35/2", "36/1", "36/2", "37/1", "37/2"]},
    "Szereg 9": {"zakres": "27-31", "lokale": ["27/1", "27/2", "28/1", "28/2", "29/1", "29/2", "30/1", "30/2", "31/1", "31/2"]},
    "Szereg 10": {"zakres": "20-26", "lokale": ["20/1", "20/2", "21/1", "21/2", "22/1", "22/2", "23/1", "23/2", "24/1", "24/2", "25/1", "25/2", "26/1", "26/2"]},
    "Szereg 11": {"zakres": "15-19", "lokale": ["15/1", "15/2", "16/1", "16/2", "17/1", "17/2", "18/1", "18/2", "19/1", "19/2"]}
}

gc = None
worksheet = None
drive_service = None
g_drive_main_folder_id = None
g_drive_szeregi_folder_id = None

def get_google_creds():
    """Obsługuje logowanie OAuth 2.0 i przechowuje token."""
    creds = None
    
    # --- SEKCJA DLA RAILWAY ---
    creds_json_string = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json_string:
        try:
            with open(GOOGLE_CREDENTIALS_FILE, 'w') as f:
                f.write(creds_json_string)
        except Exception as e:
            logger.error(f"Nie można zapisać credentials ze zmiennej: {e}")
    
    token_json_string = os.getenv('GOOGLE_TOKEN_JSON')
    if token_json_string:
        try:
            with open(GOOGLE_TOKEN_FILE, 'w') as token:
                token.write(token_json_string)
        except Exception as e:
            logger.error(f"Nie można zapisać tokenu ze zmiennej: {e}")
    # --- KONIEC SEKCJI ---
            
    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                logger.critical(f"BŁĄD KRYTYCZNY PRZY AUTORYZACJI: {e}")
                exit()

        with open(GOOGLE_TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    
    return creds

try:
    creds = get_google_creds()
    logger.info("Pomyślnie uzyskano dane logowania Google (OAuth 2.0)")
    
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    logger.info(f"Pomyślnie połączono z Arkuszem Google: {GOOGLE_SHEET_NAME}")

    drive_service = build('drive', 'v3', credentials=creds)
    
    def find_folder(folder_name):
        response_folder = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=False",
            spaces='drive',
            fields='files(id, name)',
        ).execute()
        files = response_folder.get('files', [])
        if not files:
            return None
        return files[0].get('id')

    g_drive_main_folder_id = find_folder(G_DRIVE_MAIN_FOLDER_NAME)
    g_drive_szeregi_folder_id = find_folder(G_DRIVE_SZEREGI_FOLDER_NAME)

    if not g_drive_main_folder_id:
        logger.critical(f"Nie udało się znaleźć głównego folderu '{G_DRIVE_MAIN_FOLDER_NAME}'.")
        exit()

except Exception as e:
    logger.critical(f"BŁĄD KRYTYCZNY: Nie można połączyć z Google: {e}")
    exit()


# ----------------------------------------------------
# --- 4. KONFIGURACJA GEMINI (Dopasowanie AI) ---
# ----------------------------------------------------
genai.configure(api_key=GEMINI_API_KEY)

system_instruction_text = """
Jesteś botem klasyfikującym. 
Otrzymasz listę firm (ponumerowaną) i wpis użytkownika.
Twoim zadaniem jest zwrócić TYLKO NUMER (ID) firmy, która najlepiej pasuje do wpisu.
Jeśli nic nie pasuje, zwróć -1.
"""

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={"temperature": 0.0, "max_output_tokens": 10, "response_mime_type": "text/plain"},
    safety_settings=[
        {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
    ],
    system_instruction=system_instruction_text
)

def dopasuj_firme_ai(tekst_uzytkownika: str) -> str:
    """Logika AI + Python do wyszukiwania firmy."""
    lista_indexed = "\n".join([f"{i}: {name}" for i, name in enumerate(LISTA_FIRM_WYKONAWCZYCH)])
    prompt = f"Lista firm:\n{lista_indexed}\nWpis użytkownika: \"{tekst_uzytkownika}\"\nID pasującej firmy:"

    ai_success = False
    wynik_firma = None

    try:
        response = model.generate_content(prompt)
        if response.candidates and response.candidates[0].finish_reason.value == 1:
            ai_output = response.text.strip()
            if ai_output.isdigit() or (ai_output.startswith("-") and ai_output[1:].isdigit()):
                idx = int(ai_output)
                if 0 <= idx < len(LISTA_FIRM_WYKONAWCZYCH):
                    wynik_firma = LISTA_FIRM_WYKONAWCZYCH[idx]
                    ai_success = True
    except Exception as e:
        logger.error(f"Błąd połączenia z AI: {e}")

    if ai_success and wynik_firma:
        return wynik_firma

    # Fallback Python
    search_term = tekst_uzytkownika.strip().upper()
    candidates = []
    for firm in LISTA_FIRM_WYKONAWCZYCH:
        clean_firm = firm.upper().replace("SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ", "")
        if search_term in clean_firm:
            candidates.append(firm)
    
    if len(candidates) >= 1:
        return candidates[0]

    matches = difflib.get_close_matches(search_term, [f.upper() for f in LISTA_FIRM_WYKONAWCZYCH], n=1, cutoff=0.5)
    if matches:
        for firm in LISTA_FIRM_WYKONAWCZYCH:
            if firm.upper() == matches[0]:
                return firm

    return f"INNA: {tekst_uzytkownika}"

# --- 5a. FUNKCJA MENU GŁÓWNEGO (Zależna od TRYBU) ---
def get_main_menu_keyboard(mode='add'):
    """Generuje klawiaturę główną w zależności od trybu (DODAWANIE / ARCHIWUM)."""
    if mode == 'archive':
        # Tryb CZYTANIA (Archiwum) - Kolor pomarańczowy
        return ReplyKeyboardMarkup([
            ["🔍 PRZEGLĄDAJ LOKALE"],
            ["🔄 ZMIEŃ TRYB: 🟢 DODAWANIE"]
        ], resize_keyboard=True)
    else:
        # Tryb DOMYŚLNY (Dodawanie) - Kolor zielony
        return ReplyKeyboardMarkup([
            ["📝 NOWY ODBIÓR"],
            ["🔄 ZMIEŃ TRYB: 🟠 ARCHIWUM"]
        ], resize_keyboard=True)

# --- Funkcja tworząca klawiaturę Inline ---
def get_inline_keyboard(usterka_id=None, context: ContextTypes.DEFAULT_TYPE = None):
    """Tworzy i zwraca dynamiczną klawiaturę inline na podstawie stanu sesji."""
    keyboard = []
    chat_data = context.chat_data if context else {}
    
    lista_lokali = chat_data.get('lista_lokali_szeregu')
    if lista_lokali:
        row = []
        for lokal_name in lista_lokali:
            row.append(InlineKeyboardButton(lokal_name, callback_data=f"setlokal_{lokal_name}"))
            if len(row) >= 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        
        # Jeśli jesteśmy w trybie ARCHIWUM, napis jest inny
        if chat_data.get('app_mode') == 'archive':
             keyboard.append([InlineKeyboardButton("--- Kliknij lokal, by sprawdzić ---", callback_data="noop")])
        else:
             keyboard.append([InlineKeyboardButton("--- Wybierz lokal powyżej ---", callback_data="noop")])

    # Przyciski funkcyjne tylko w trybie DODAWANIA
    if chat_data.get('app_mode') != 'archive':
        if usterka_id:
            keyboard.append([
                InlineKeyboardButton(f"Cofnij TĘ usterkę ↩️", callback_data=f'cofnij_{usterka_id}')
            ])
        
        keyboard.append([
            InlineKeyboardButton("Zakończ Cały Odbiór 🏁", callback_data='koniec_odbioru')
        ])
    else:
        # W trybie archiwum przycisk powrotu
        keyboard.append([InlineKeyboardButton("<< Wróć do menu", callback_data="start_menu")])
    
    return InlineKeyboardMarkup(keyboard)


# -----------------------------------------------------------
# --- 6. OBSŁUGA ARKUSZA (ZAPIS i ODCZYT) ---
# -----------------------------------------------------------
KOLUMNA_DATA = 'A'
KOLUMNA_LOKAL = 'B'
KOLUMNA_USTERKA = 'C'
KOLUMNA_PODMIOT = 'D'
KOLUMNA_ZDJECIE = 'E'
NUMER_KOLUMNY_KLUCZOWEJ = 1 

def zapisz_w_arkuszu(dane_json: dict, data_telegram: datetime) -> bool:
    """Zapisuje dane w arkuszu roboczym."""
    try:
        data_str = data_telegram.strftime('%Y-%m-%d %H:%M:%S')
        wartosci_w_kolumnie = worksheet.col_values(NUMER_KOLUMNY_KLUCZOWEJ)
        pierwszy_wolny_wiersz = len(wartosci_w_kolumnie) + 1
        
        updates = [
            {'range': f'{KOLUMNA_DATA}{pierwszy_wolny_wiersz}', 'values': [[data_str]]},
            {'range': f'{KOLUMNA_LOKAL}{pierwszy_wolny_wiersz}', 'values': [[dane_json.get('numer_lokalu_budynku', 'BŁĄD')]]},
            {'range': f'{KOLUMNA_USTERKA}{pierwszy_wolny_wiersz}', 'values': [[dane_json.get('rodzaj_usterki', 'BŁĄD')]]},
            {'range': f'{KOLUMNA_PODMIOT}{pierwszy_wolny_wiersz}', 'values': [[dane_json.get('podmiot_odpowiedzialny', 'BŁĄD')]]},
            {'range': f'{KOLUMNA_ZDJECIE}{pierwszy_wolny_wiersz}', 'values': [[dane_json.get('link_do_zdjecia', '')]]}
        ]
        worksheet.batch_update(updates, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        logger.error(f"Błąd podczas zapisu do Google Sheets: {e}")
        return False

def pobierz_usterki_z_archiwum(numer_lokalu: str) -> str:
    """Pobiera usterki dla danego lokalu z arkusza Archiwum."""
    try:
        # Otwieramy arkusz Archiwum
        try:
            archive_sheet = spreadsheet.worksheet(WORKSHEET_ARCHIVE_NAME)
        except gspread.WorksheetNotFound:
            return f"❌ BŁĄD: Nie znaleziono arkusza o nazwie '{WORKSHEET_ARCHIVE_NAME}'."

        # Pobieramy wszystkie dane 
        rows = archive_sheet.get_all_values()
        
        znalezione_usterki = []
        
        # Iterujemy (pomijamy ewentualny nagłówek w wierszu 0, jeśli dane zaczynają się od 1)
        for index, row in enumerate(rows):
            if index == 0: continue # Zakładamy nagłówek w 1 wierszu

            # Sprawdzamy czy wiersz ma wystarczająco kolumn (index 1 to kolumna B - Lokal)
            # Kolumny: 0=Data, 1=Lokal, 2=Usterka, 3=Podmiot, 4=Zdjęcie
            if len(row) > 1 and row[1] == numer_lokalu:
                data_wpisu = row[0] if len(row) > 0 else "-"
                usterka = row[2] if len(row) > 2 else "Brak opisu"
                podmiot = row[3] if len(row) > 3 else "-"
                zdjecie = row[4] if len(row) > 4 else ""
                
                wpis = f"🔹 <b>{usterka}</b>\n   👷 {podmiot}\n   📅 {data_wpisu}"
                if zdjecie:
                    wpis += f"\n   📷 <a href='{zdjecie}'>Zobacz zdjęcie</a>"
                
                znalezione_usterki.append(wpis)
                
        if not znalezione_usterki:
            return f"📂 Brak usterek w Archiwum dla lokalu: <b>{numer_lokalu}</b>"
            
        naglowek = f"📂 <b>ARCHIWUM: {numer_lokalu}</b> (Znaleziono: {len(znalezione_usterki)})\n" + "-"*25 + "\n\n"
        return naglowek + "\n\n".join(znalezione_usterki)

    except Exception as e:
        logger.error(f"Błąd odczytu z archiwum: {e}")
        return "❌ Wystąpił błąd podczas pobierania danych z Archiwum."


# --- FUNKCJA WYSYŁANIA NA GOOGLE DRIVE ---
def upload_photo_to_drive(file_bytes, target_name, usterka_name, podmiot_name, tryb_odbioru='lokal'):
    global drive_service, g_drive_main_folder_id, G_DRIVE_MAIN_FOLDER_NAME
    try:
        parent_folder_id = g_drive_main_folder_id
        q_str = f"name='{target_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_folder_id}' in parents and trashed=False"
        response = drive_service.files().list(q=q_str, spaces='drive', fields='files(id, name)').execute()
        target_folder = response.get('files', [])

        if not target_folder:
            folder_metadata = {'name': target_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_folder_id]}
            created_folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            target_folder_id = created_folder.get('id')
        else:
            target_folder_id = target_folder[0].get('id')
        
        file_name = f"{usterka_name} - {podmiot_name}.jpg"
        file_metadata = {'name': file_name, 'parents': [target_folder_id]}
        
        file_bytes.seek(0)
        media = MediaIoBaseUpload(file_bytes, mimetype='image/jpeg', resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        return True, file_name, file.get('id')
    except Exception as e:
        logger.error(f"Błąd Drive: {e}")
        return False, str(e), None

def delete_file_from_drive(file_id):
    try:
        drive_service.files().delete(fileId=file_id).execute()
        return True, None
    except Exception as e:
        return False, str(e)


# --- Funkcje pomocnicze klawiatury ---
def build_szereg_keyboard():
    keyboard = []
    row = []
    sorted_keys = sorted(DANE_SZEREGOW.keys(), key=lambda x: int(x.split(' ')[1]))
    for szereg_name in sorted_keys:
        dane = DANE_SZEREGOW[szereg_name]
        button_text = f"{szereg_name} ({dane['zakres']})"
        row.append(InlineKeyboardButton(button_text, callback_data=f"szereg_{szereg_name}"))
        if len(row) >= 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("<< Anuluj", callback_data="start_menu")])
    return InlineKeyboardMarkup(keyboard)


# --- Handler komendy /start ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_data = context.chat_data
    
    # Domyślny tryb to 'add' (dodawanie)
    if 'app_mode' not in chat_data:
        chat_data['app_mode'] = 'add'
    
    # Jeśli w trakcie odbioru (tylko w trybie add), ostrzeżenie
    if chat_data.get('odbiur_aktywny') and chat_data.get('app_mode') == 'add':
        await update.message.reply_text(
            "Odbiór jest już w toku. Zakończ go, aby rozpocząć nowy.",
            reply_markup=get_inline_keyboard(usterka_id=None, context=context)
        )
    else:
        # Jeśli nie ma aktywnego odbioru, czyścimy zbędne dane (ale nie tryb)
        mode = chat_data['app_mode']
        chat_data.clear()
        chat_data['app_mode'] = mode
        
        msg = "Witaj! Wybierz działanie z menu poniżej."
        await update.message.reply_text(msg, reply_markup=get_main_menu_keyboard(mode))


# --- 7. Główny Handler (serce bota) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or (not update.message.text and not update.message.caption):
         return

    user_message = update.message.text
    chat_data = context.chat_data
    
    # Inicjalizacja trybu
    if 'app_mode' not in chat_data:
        chat_data['app_mode'] = 'add'

    # --- OBSŁUGA ZMIANY TRYBU ---
    if user_message == "🔄 ZMIEŃ TRYB: 🟢 DODAWANIE":
        chat_data['app_mode'] = 'archive'
        await update.message.reply_text(
            "🟠 Przełączono w tryb: ARCHIWUM (Tylko odczyt).",
            reply_markup=get_main_menu_keyboard('archive')
        )
        return

    if user_message == "🔄 ZMIEŃ TRYB: 🟠 ARCHIWUM":
        chat_data['app_mode'] = 'add'
        await update.message.reply_text(
            "🟢 Przełączono w tryb: DODAWANIE (Zapis usterek).",
            reply_markup=get_main_menu_keyboard('add')
        )
        return

    # --- SCENARIUSZ 0: START (Zależnie od nazwy przycisku) ---
    if user_message in ["📝 NOWY ODBIÓR", "🔍 PRZEGLĄDAJ LOKALE", "NOWY ODBIÓR"]:
        # Czyścimy stare dane sesji, ale zachowujemy tryb
        current_mode = chat_data['app_mode']
        # Reset
        chat_data.clear()
        chat_data['app_mode'] = current_mode
        
        keyboard = build_szereg_keyboard()
        tekst_powitalny = "Tryb: 📝 NOWY ODBIÓR" if current_mode == 'add' else "Tryb: 🔍 PRZEGLĄDANIE ARCHIWUM"
        
        await update.message.reply_text(
            f"{tekst_powitalny}.\nWybierz Szereg:",
            reply_markup=keyboard
        )
        return

    # --- LOGIKA TYLKO DLA TRYBU DODAWANIA (add) ---
    if chat_data.get('app_mode') == 'add':
        
        # STAN: Oczekiwanie na firmę
        if chat_data.get('state') == 'AWAITING_FIRMA_SZEREG':
            if not user_message:
                 await update.message.reply_text("Oczekuję na nazwę firmy...")
                 return

            wpis_usera = user_message.strip()
            await update.message.reply_text(f"🔎 Szukam firmy pasującej do: '{wpis_usera}'...")
            firma = dopasuj_firme_ai(wpis_usera)
            szereg_name = chat_data.get('wybrany_szereg', 'BŁĄD STANU')
            
            if szereg_name == 'BŁĄD STANU':
                await update.message.reply_text("Wystąpił błąd stanu. Spróbuj ponownie od /start")
                chat_data.clear()
                return
            
            target_name = szereg_name.upper().strip()
            
            chat_data['odbiur_aktywny'] = True
            chat_data['odbiur_identyfikator'] = target_name 
            chat_data['odbiur_target_nazwa_do_zdjec'] = None
            chat_data['tryb_odbioru'] = "szereg"
            chat_data['odbiur_podmiot'] = firma
            chat_data['odbiur_wpisy'] = []
            chat_data['state'] = None
            
            chat_data['lista_lokali_szeregu'] = DANE_SZEREGOW[szereg_name].get("lokale", [])
            chat_data['biezacy_lokal_w_szeregu'] = None 

            await update.message.reply_text("Rozpoczynam odbiór...", reply_markup=ReplyKeyboardRemove())
            
            await update.message.reply_text(
                f"✅ Rozpoczęto odbiór dla: <b>CAŁY {target_name}</b>\n"
                f"Wykonawca: <b>{firma}</b>\n\n"
                f"Teraz <b>koniecznie wybierz lokal z przycisków poniżej</b> i wpisuj usterki.\n",
                reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                parse_mode='HTML'
            )
            return

        # --- Obsługa wpisywania usterek ---
        # (Tylko jeśli nie jest to komenda zmiany trybu, która już obsłużyliśmy wyżej)
        if user_message and not user_message.startswith("🔄"):
            message_time = update.message.date

            # Odbiór aktywny - usterka tekstowa
            if chat_data.get('odbiur_aktywny'):
                logger.info(f"Odbiór aktywny. Zapisywanie usterki tekstowej.")
                usterka_opis_raw = user_message.strip()
                prefix_lokalu = chat_data.get('biezacy_lokal_w_szeregu')
                
                if not prefix_lokalu:
                    await update.message.reply_text(
                        "❌ BŁĄD: Nie wybrano lokalu.\nProszę, <b>wybierz lokal z przycisków poniżej</b>.",
                        reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                        parse_mode='HTML'
                    )
                    return

                usterka_opis = f"{prefix_lokalu} - {usterka_opis_raw}"
                usterka_id = str(uuid.uuid4())
                nowy_wpis = {'id': usterka_id, 'typ': 'tekst', 'opis': usterka_opis}
                chat_data['odbiur_wpisy'].append(nowy_wpis)
                
                await update.message.reply_text(
                    f"➕ Dodano: <b>{usterka_opis}</b>\n(Łącznie: {len(chat_data['odbiur_wpisy'])}).",
                    reply_markup=get_inline_keyboard(usterka_id=usterka_id, context=context),
                    parse_mode='HTML'
                )
                return
    
    # Jeśli tryb Archive i ktoś coś pisze (ignorujemy lub podpowiadamy)
    if chat_data.get('app_mode') == 'archive' and user_message and not user_message.startswith("🔄"):
         await update.message.reply_text(
             "Jesteś w trybie ARCHIWUM (tylko odczyt).\nUżyj przycisków, aby przeglądać lokale lub zmień tryb.",
             reply_markup=get_main_menu_keyboard('archive')
         )


# --- 7b. HANDLER DLA ZDJĘĆ ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_data = context.chat_data
    
    # Zabezpieczenie: Zdjęcia tylko w trybie ADD i przy aktywnym odbiorze
    if chat_data.get('app_mode') != 'add':
        await update.message.reply_text("Przełącz się w tryb DODAWANIA, aby wysyłać zdjęcia.", reply_markup=get_main_menu_keyboard('archive'))
        return

    if not chat_data.get('odbiur_aktywny'):
        await update.message.reply_text("Wyślij zdjęcie *po* rozpoczęciu odbioru.", reply_markup=get_main_menu_keyboard('add'))
        return

    usterka_opis_raw = update.message.caption
    if not usterka_opis_raw:
        await update.message.reply_text("❌ Zdjęcie musi mieć opis (usterkę)!", reply_markup=get_inline_keyboard(usterka_id=None, context=context))
        return

    podmiot = chat_data.get('odbiur_podmiot')
    tryb = chat_data.get('tryb_odbioru')
    prefix_lokalu = chat_data.get('biezacy_lokal_w_szeregu')
    
    if prefix_lokalu:
        target_folder_name = prefix_lokalu.replace('/', '.')
        opis_do_arkusza = f"{prefix_lokalu} - {usterka_opis_raw} (zdjęcie)"
    else:
        await update.message.reply_text("❌ BŁĄD: Nie wybrano lokalu.", reply_markup=get_inline_keyboard(usterka_id=None, context=context))
        return

    await update.message.reply_text(f"Wysyłam zdjęcie do folderu: <b>{target_folder_name}</b>...", parse_mode='HTML')

    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes_io = io.BytesIO()
        await photo_file.download_to_memory(file_bytes_io)
        
        success, message, file_id = upload_photo_to_drive(
            file_bytes_io, target_folder_name, usterka_opis_raw.strip(), podmiot, tryb_odbioru=tryb
        )
        
        if success:
            usterka_id = str(uuid.uuid4())
            nowy_wpis = {'id': usterka_id, 'typ': 'zdjecie', 'opis': opis_do_arkusza, 'file_id': file_id}
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            await update.message.reply_text(
                f"✅ Zdjęcie zapisane. ➕ Usterka dodana.\n(Łącznie: {len(chat_data['odbiur_wpisy'])}).",
                reply_markup=get_inline_keyboard(usterka_id=usterka_id, context=context),
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(f"❌ Błąd Google Drive: {message}")
            
    except Exception as e:
        logger.error(f"Błąd zdjęcia: {e}")
        await update.message.reply_text(f"❌ Wystąpił błąd: {e}")


# --- 7c. HANDLER: Obsługa przycisków Inline ---
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_data = context.chat_data
    data = query.data

    if data == 'start_menu':
        if chat_data.get('odbiur_aktywny'):
            await query.answer("Odbiór jest aktywny. Zakończ go.", show_alert=True)
            return
        
        mode = chat_data.get('app_mode', 'add')
        chat_data.clear()
        chat_data['app_mode'] = mode
        try:
            await query.edit_message_text("Anulowano.", reply_markup=None)
        except: pass
        await query.message.reply_text("Menu Główne:", reply_markup=get_main_menu_keyboard(mode))
        return

    # --- Wybór Szeregu (Wspólne dla obu trybów, ale inna reakcja) ---
    elif data.startswith('szereg_'):
        szereg_name = data.split('_', 1)[1]
        chat_data['wybrany_szereg'] = szereg_name
        
        # !!! SPRAWDZAMY TRYB !!!
        if chat_data.get('app_mode') == 'archive':
            # ARCHIWUM: Pomijamy firmę, ładujemy lokale
            chat_data['lista_lokali_szeregu'] = DANE_SZEREGOW[szereg_name].get("lokale", [])
            
            await query.edit_message_text(
                f"📂 <b>ARCHIWUM: {szereg_name}</b>\n\n"
                f"Kliknij w numer lokalu, aby zobaczyć historię usterek:",
                reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                parse_mode='HTML'
            )
            return
        else:
            # DODAWANIE: Pytamy o firmę
            if chat_data.get('odbiur_aktywny'):
                await query.answer("Odbiór aktywny.", show_alert=True)
                return

            chat_data['state'] = 'AWAITING_FIRMA_SZEREG'
            await query.edit_message_text(
                f"Wybrano: <b>CAŁY {szereg_name}</b>\n"
                f"Podaj nazwę firmy:",
                parse_mode='HTML'
            )
            return
    
    # --- Wybór Lokalu (Wspólne dla obu trybów) ---
    elif data.startswith("setlokal_"):
        lokal_name = data.split('_', 1)[1]
        
        # !!! TRYB ARCHIWUM !!!
        if chat_data.get('app_mode') == 'archive':
            await query.answer(f"Pobieram dane dla {lokal_name}...")
            # Pobieramy dane
            raport = pobierz_usterki_z_archiwum(lokal_name)
            # Wysyłamy jako nową wiadomość, aby nie psuć menu nawigacji
            await query.message.reply_text(raport, parse_mode='HTML', disable_web_page_preview=False)
            return
        
        # !!! TRYB DODAWANIA !!!
        else:
            if not chat_data.get('odbiur_aktywny'):
                 await query.message.reply_text("Sesja nieaktywna.", reply_markup=get_main_menu_keyboard('add'))
                 return
            
            chat_data['biezacy_lokal_w_szeregu'] = lokal_name
            await query.answer(f"Lokal: {lokal_name}")
            try:
                nowy_tekst = f"Aktywny lokal: <b>{lokal_name}</b>\n(Wybrano: {datetime.now().strftime('%H:%M:%S')})"
                await query.edit_message_text(
                    nowy_tekst,
                    reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                    parse_mode='HTML'
                )
            except Exception: pass
            return
    
    # --- Obsługa przycisków funkcyjnych (Tylko tryb ADD) ---
    elif data.startswith('cofnij_'):
        id_to_delete = data.split('_', 1)[1]
        wpisy_lista = chat_data.get('odbiur_wpisy', [])
        wpis_to_delete = next((w for w in wpisy_lista if w.get('id') == id_to_delete), None)

        if wpis_to_delete:
            wpisy_lista.remove(wpis_to_delete)
            chat_data['odbiur_wpisy'] = wpisy_lista
            delete_info = f"↩️ Usunięto: <b>{wpis_to_delete.get('opis')}</b>"
            
            if wpis_to_delete.get('typ') == 'zdjecie' and wpis_to_delete.get('file_id'):
                ok, err = delete_file_from_drive(wpis_to_delete['file_id'])
                delete_info += " (Drive OK)" if ok else f" (Drive Error: {err})"
            
            try: await query.edit_message_text(f"--- USUNIĘTO ---", reply_markup=None)
            except: pass
            
            await query.message.reply_text(
                f"{delete_info}\n(Pozostało: {len(wpisy_lista)})",
                reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                parse_mode='HTML'
            )
        else:
            await query.answer("Już usunięto.", show_alert=True)

    elif data == 'koniec_odbioru':
        identyfikator = chat_data.get('odbiur_identyfikator', '')
        podmiot = chat_data.get('odbiur_podmiot')
        wpisy = chat_data.get('odbiur_wpisy', [])
        msg_time = datetime.now()
        
        if not wpisy:
            await query.message.reply_text("Zakończono bez usterek.", reply_markup=get_main_menu_keyboard('add'))
        else:
            licznik = 0
            for wpis in wpisy:
                opis = wpis.get('opis', '')
                lokal = identyfikator
                usterka = opis
                if ' - ' in opis:
                    parts = opis.split(' - ', 1)
                    if len(parts) == 2: lokal, usterka = parts[0], parts[1]
                
                dane_json = {
                    "numer_lokalu_budynku": lokal,
                    "rodzaj_usterki": usterka,
                    "podmiot_odpowiedzialny": podmiot,
                    "link_do_zdjecia": ""
                }
                if wpis.get('file_id'):
                    dane_json['link_do_zdjecia'] = f"https://drive.google.com/file/d/{wpis['file_id']}/view"
                
                if zapisz_w_arkuszu(dane_json, msg_time):
                    licznik += 1
            
            await query.message.reply_text(
                f"✅ Zakończono. Zapisano {licznik}/{len(wpisy)} usterek dla {identyfikator}.",
                reply_markup=get_main_menu_keyboard('add')
            )
        
        chat_data.clear()
        # Przywracamy tryb add
        chat_data['app_mode'] = 'add'
        try: await query.edit_message_reply_markup(reply_markup=None)
        except: pass

    elif data == "noop":
        await query.answer()
        return


# --- 8. Uruchomienie Bota ---
def main():
    logger.info("Uruchamianie bota...")
    
    PORT = int(os.environ.get('PORT', 8443))
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN') or os.environ.get('WEBHOOK_URL')
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    if domain:
        WEBHOOK_URL = f"https://{domain}"
        logger.info(f"Webhook: {WEBHOOK_URL}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
        )
    else:
        logger.warning("Brak domeny Webhook, uruchamianie w trybie Polling (lokalnie).")
        application.run_polling()

if __name__ == '__main__':
    main()
