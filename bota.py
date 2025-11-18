import os
import json
import logging
import io
import uuid
from datetime import datetime
from dotenv import load_dotenv
import difflib # Import dla dopasowywania ciągu

# --- Importy Bibliotek (Usuwamy Google Gemini) ---
# Usunięto: import google.generativeai as genai
# Usunięto: from google.generativeai.types import HarmCategory, HarmBlockThreshold
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
# Usunięto: GEMINI_API_KEY (już niepotrzebny)

if not TELEGRAM_TOKEN:
    logger.critical("BŁĄD: Nie znaleziono tokenu TELEGRAM_TOKEN w pliku .env")
    exit()

# --- 3. Konfiguracja Google ---
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
GOOGLE_TOKEN_FILE = 'token.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
GOOGLE_SHEET_NAME = 'Odbiory_Kolonia_Warszawska'
WORKSHEET_NAME = 'Arkusz1'
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
    "PRIMA TYNK Janusz Pelc"
    
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

START_KEYBOARD = ReplyKeyboardMarkup(
    [["NOWY ODBIÓR"]], resize_keyboard=True
)

gc = None
worksheet = None
drive_service = None
g_drive_main_folder_id = None
g_drive_szeregi_folder_id = None

# --- FUNKCJA WYSZUKUJĄCA FIRMĘ (ZAMIAST AI - STABILNY PYTHON) ---
def dopasuj_firme_python(tekst_uzytkownika: str) -> str:
    """
    Używa dopasowania ciągu znaków (difflib) do znalezienia najlepiej pasującej firmy.
    Minimalny próg dopasowania: 0.6 (60%).
    """
    search_term = tekst_uzytkownika.strip().upper()
    
    OFFICIAL_LIST = LISTA_FIRM_WYKONAWCZYCH 
    
    # Tworzenie mapowania czystej nazwy na oryginalną nazwę (usuwanie dużych spólek/znaków)
    searchable_list = []
    mapping = {}

    for firm_name in OFFICIAL_LIST:
        # Normalizacja: usunięcie sp. z o.o. i konwersja na duże litery dla lepszego porównania
        clean_name = firm_name.upper().replace('SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ', '').strip()
        searchable_list.append(clean_name)
        mapping[clean_name] = firm_name # Mapowanie czystej nazwy na oryginalną

    # 1. Użycie difflib.get_close_matches (n=1, cutoff=0.6)
    matches = difflib.get_close_matches(
        search_term,
        searchable_list,
        n=1,
        cutoff=0.6 
    )

    if matches:
        best_match_cleaned = matches[0]
        # Zwracamy oryginalną nazwę z listy
        final_firm = mapping.get(best_match_cleaned, matches[0])
        logger.info(f"Python dopasował '{tekst_uzytkownika}' do '{final_firm}' (Found match)")
        return final_firm
    else:
        # 2. Fallback: Jeśli dopasowanie jest słabe (poniżej 60%) lub brak, używamy surowego wpisu
        result = f"INNA: {tekst_uzytkownika}"
        logger.warning(f"Python nie znalazł dopasowania dla '{tekst_uzytkownika}', użyto fallback.")
        return result

# Zastępujemy starą nazwę funkcji dla czytelności w handlerach
dopasuj_firme_ai = dopasuj_firme_python


def get_google_creds():
    """Obsługuje logowanie OAuth 2.0 i przechowuje token."""
    creds = None
    
    # --- SEKCJA DLA RAILWAY ---
    creds_json_string = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json_string:
        logger.info("Wykryto credentials w zmiennej środowiskowej. Zapisywanie do pliku...")
        try:
            with open(GOOGLE_CREDENTIALS_FILE, 'w') as f:
                f.write(creds_json_string)
            logger.info(f"Pomyślnie zapisano credentials w {GOOGLE_CREDENTIALS_FILE}")
        except Exception as e:
            logger.error(f"Nie można zapisać credentials ze zmiennej: {e}")
    
    token_json_string = os.getenv('GOOGLE_TOKEN_JSON')
    if token_json_string:
        logger.info("Wykryto token w zmiennej środowiskowej. Zapisywanie do pliku...")
        try:
            with open(GOOGLE_TOKEN_FILE, 'w') as token:
                token.write(token_json_string)
            logger.info(f"Pomyślnie zapisano token w {GOOGLE_TOKEN_FILE}")
        except Exception as e:
            logger.error(f"Nie można zapisać tokenu ze zmiennej: {e}")
    # --- KONIEC SEKCJI ---
            
    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Token wygasł, odświeżanie...")
            creds.refresh(Request())
        else:
            logger.info("Brak tokenu lub token nieprawidłowy. Uruchamianie przepływu autoryzacji...")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                logger.critical(f"BŁĄD KRYTYCZNY PRZY AUTORYZACJI: {e}")
                exit()

        with open(GOOGLE_TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        logger.info(f"Pomyślnie zapisano/zaktualizowano token w {GOOGLE_TOKEN_FILE}")
    
    return creds

try:
    creds = get_google_creds()
    logger.info("Pomyślnie uzyskano dane logowania Google (OAuth 2.0)")
    
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    logger.info(f"Pomyślnie połączono z Arkuszem Google: {GOOGLE_SHEET_NAME}")

    drive_service = build('drive', 'v3', credentials=creds)
    logger.info("Pomyślnie połączono z Google Drive")

    def find_folder(folder_name):
        logger.info(f"Szukanie folderu: '{folder_name}'...")
        response_folder = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=False",
            spaces='drive',
            fields='files(id, name)',
        ).execute()
        
        files = response_folder.get('files', [])
        if not files:
            logger.critical(f"BŁĄD KRYTYCZNY: Nie znaleziono folderu '{folder_name}' na Twoim 'Mój Dysk'!")
            return None
        
        folder_id = files[0].get('id')
        logger.info(f"Pomyślnie znaleziono folder '{folder_name}' (ID: {folder_id})")
        return folder_id

    g_drive_main_folder_id = find_folder(G_DRIVE_MAIN_FOLDER_NAME)
    g_drive_szeregi_folder_id = find_folder(G_DRIVE_SZEREGI_FOLDER_NAME)

    if not g_drive_main_folder_id:
        logger.critical(f"Nie udało się znaleźć głównego folderu '{G_DRIVE_MAIN_FOLDER_NAME}'. Zamykanie.")
        exit()

except Exception as e:
    logger.critical(f"BŁĄD KRYTYCZNY: Nie można połączyć z Google: {e}")
    exit()


# --- Funkcja tworząca klawiaturę Inline ---
def get_inline_keyboard(usterka_id=None, context: ContextTypes.DEFAULT_TYPE = None):
    """Tworzy i zwraca dynamiczną klawiaturę inline na podstawie stanu sesji."""
    keyboard = []
    
    if context:
        chat_data = context.chat_data
        
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
            keyboard.append([InlineKeyboardButton("--- Wybierz lokal powyżej ---", callback_data="noop")])

    if usterka_id:
        keyboard.append([
            InlineKeyboardButton(f"Cofnij TĘ usterkę ↩️", callback_data=f'cofnij_{usterka_id}')
        ])
    
    keyboard.append([
        InlineKeyboardButton("Zakończ Cały Odbiór 🏁", callback_data='koniec_odbioru')
    ])
    
    return InlineKeyboardMarkup(keyboard)


# --- 6. Funkcja do Zapisu w Arkuszu ---
def zapisz_w_arkuszu(dane_json: dict, data_telegram: datetime) -> bool:
    """Zapisuje przeanalizowane dane w nowym wierszu Arkusza Google."""
    try:
        data_str = data_telegram.strftime('%Y-%m-%d %H:%M:%S')
        
        nowy_wiersz = [
            data_str,
            dane_json.get('numer_lokalu_budynku', 'BŁĄD'),
            dane_json.get('rodzaj_usterki', 'BŁĄD'),
            dane_json.get('podmiot_odpowiedzialny', 'BŁĄD'),
            dane_json.get('link_do_zdjecia', '')
        ]
        
        worksheet.append_row(nowy_wiersz, value_input_option='USER_ENTERED')
        logger.info(f"Dodano wiersz do arkusza: {nowy_wiersz}")
        return True
    except Exception as e:
        logger.error(f"Błąd podczas zapisu do Google Sheets: {e}")
        return False

# --- FUNKCJA WYSYŁANIA NA GOOGLE DRIVE ---
def upload_photo_to_drive(file_bytes, target_name, usterka_name, podmiot_name, tryb_odbioru='lokal'):
    """Wyszukuje podfolder (lokalu lub szeregu) i wysyła do niego zdjęcie."""
    global drive_service, g_drive_main_folder_id, G_DRIVE_MAIN_FOLDER_NAME
    
    try:
        parent_folder_id = g_drive_main_folder_id
        
        q_str = f"name='{target_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_folder_id}' in parents and trashed=False"
        
        response = drive_service.files().list(
            q=q_str,
            spaces='drive',
            fields='files(id, name)',
        ).execute()
        
        target_folder = response.get('files', [])

        if not target_folder:
            logger.warning(f"Nie znaleziono folderu '{target_name}'. Tworzenie nowego...")
            try:
                folder_metadata = {
                    'name': target_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_folder_id]
                }
                created_folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
                target_folder_id = created_folder.get('id')
                logger.info(f"Pomyślnie utworzono folder '{target_name}' (ID: {target_folder_id})")
            except Exception as e:
                logger.error(f"KRYTYCZNY BŁĄD: Nie można utworzyć folderu na Drive: {e}")
                return False, f"Błąd tworzenia folderu na Drive: {e}", None
        else:
            target_folder_id = target_folder[0].get('id')
        
        file_name = f"{usterka_name} - {podmiot_name}.jpg"
        file_metadata = {
            'name': file_name,
            'parents': [target_folder_id]
        }
        
        file_bytes.seek(0)
        media = MediaIoBaseUpload(file_bytes, mimetype='image/jpeg', resumable=True)
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
        ).execute()
        
        file_id = file.get('id')
        logger.info(f"Pomyślnie wysłano plik '{file_name}' do folderu '{target_name}' (ID: {file_id})")
        return True, file_name, file_id
    
    except Exception as e:
        logger.error(f"Błąd podczas wysyłania na Google Drive: {e}")
        return False, str(e), None


# --- Funkcja do usuwania pliku z Google Drive ---
def delete_file_from_drive(file_id):
    """Usuwa plik z Google Drive na podstawie jego ID."""
    global drive_service
    if not file_id:
        logger.warning("Próba usunięcia pliku, ale brak file_id.")
        return False, "Brak ID pliku"
        
    try:
        drive_service.files().delete(fileId=file_id).execute()
        logger.info(f"Pomyślnie usunięto plik z Drive (ID: {file_id})")
        return True, None
    except Exception as e:
        logger.error(f"Błąd podczas usuwania pliku {file_id} z Drive: {e}")
        return False, str(e)


# --- 6b. Funkcje do budowania klawiatur dynamicznych ---

def build_szereg_keyboard():
    """Tworzy klawiaturę wyboru Szeregu (z ZAKRESEM)."""
    keyboard = []
    row = []
    sorted_keys = sorted(DANE_SZEREGOW.keys(), key=lambda x: int(x.split(' ')[1]))
    
    for szereg_name in sorted_keys:
        dane = DANE_SZEREGOW[szereg_name]
        zakres = dane['zakres']
        button_text = f"{szereg_name} ({zakres})"
        
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
    """Obsługuje komendę /start, pokazując klawiaturę główną."""
    chat_data = context.chat_data
    
    if chat_data.get('odbiur_aktywny'):
        await update.message.reply_text(
            "Odbiór jest już w toku. Zakończ go, aby rozpocząć nowy.",
            reply_markup=get_inline_keyboard(usterka_id=None, context=context)
        )
    else:
        chat_data.clear() 
        await update.message.reply_text(
            "Witaj! Bot jest gotowy.\nUżyj przycisku 'NOWY ODBIÓR' na klawiaturze, aby rozpocząć.",
            reply_markup=START_KEYBOARD
        )


# --- 7. Główny Handler (serce bota) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Przechwytuje wiadomość, sprawdza stan sesji i decyduje co robić."""
    
    if not update.message or (not update.message.text and not update.message.caption):
         logger.warning("Otrzymano pustą wiadomość (np. naklejkę). Ignorowanie.")
         return

    user_message = update.message.text
    chat_data = context.chat_data

    # --- SCENARIUSZ 0: Użytkownik klika "NOWY ODBIÓR" ---
    if user_message == "NOWY ODBIÓR":
        if chat_data.get('odbiur_aktywny'):
            await update.message.reply_text(
                "Odbiór jest już w toku. Zakończ go, aby rozpocząć nowy.",
                reply_markup=get_inline_keyboard(usterka_id=None, context=context)
            )
        else:
            chat_data.clear()
            keyboard = build_szereg_keyboard()
            await update.message.reply_text(
                "Tryb: Nowy Odbiór.\nWybierz, który szereg chcesz odbierać:",
                reply_markup=keyboard
            )
        return

    # --- STAN: Oczekiwanie na firmę (po wybraniu szeregu) ---
    if chat_data.get('state') == 'AWAITING_FIRMA_SZEREG':
        if not user_message:
             await update.message.reply_text("Oczekuję na nazwę firmy...")
             return

        wpis_usera = user_message.strip()
        
        # --- UŻYCIE WBUDOWANEGO DOPASOWANIA PYTHONA ZAMIAST AI ---
        firma = dopasuj_firme_python(wpis_usera)
        # --------------------------------------------------------

        szereg_name = chat_data.get('wybrany_szereg', 'BŁĄD STANU')
        
        if szereg_name == 'BŁĄD STANU':
            await update.message.reply_text("Wystąpił błąd stanu. Spróbuj ponownie od /start", reply_markup=START_KEYBOARD)
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
        
        await update.message.reply_text(f"✅ Rozpoczęto odbiór dla: <b>CAŁY {target_name}</b>\n"
                                        f"Wykonawca: <b>{firma}</b>\n\n"
                                        f"Teraz <b>koniecznie wybierz lokal z przycisków poniżej</b> i wpisuj usterki.\n",
                                        reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                                        parse_mode='HTML')
        return

    # --- Reszta logiki ---
    if not user_message:
        if update.message.caption:
            logger.info("Wiadomość tekstowa jest pusta, ale jest caption. Przekazuję do handle_photo.")
            return
        else:
            logger.warning("Otrzymano wiadomość bez tekstu i bez caption. Ignorowanie.")
            return

    message_time = update.message.date

    try:
        # --- LOGIKA SESJI ODBIORU ---

        # SCENARIUSZ 1: Użytkownik KOŃCZY odbiór (Fallback tekstowy)
        if user_message.lower().strip() == 'koniec odbioru':
            if chat_data.get('odbiur_aktywny'):
                identyfikator_odbioru = chat_data.get('odbiur_identyfikator', 'Brak ID Odbioru')
                podmiot = chat_data.get('odbiur_podmiot')
                wpisy_lista = chat_data.get('odbiur_wpisy', [])
                
                if not wpisy_lista:
                    await update.message.reply_text(f"Zakończono odbiór dla {identyfikator_odbioru}. Nie dodano żadnych usterek.",
                                                    reply_markup=START_KEYBOARD)
                else:
                    logger.info(f"Zapisywanie {len(wpisy_lista)} usterek dla {identyfikator_odbioru}...")
                    licznik_zapisanych = 0
                    
                    for wpis in wpisy_lista:
                        opis_caly = wpis.get('opis', 'BŁĄD WPISU')
                        
                        lokal_dla_wpisu = identyfikator_odbioru 
                        usterka_dla_wpisu = opis_caly

                        if ' - ' in opis_caly:
                            parts = opis_caly.split(' - ', 1)
                            if len(parts) == 2:
                                lokal_dla_wpisu = parts[0]
                                usterka_dla_wpisu = parts[1]
                        
                        dane_json = {
                            "numer_lokalu_budynku": lokal_dla_wpisu,
                            "rodzaj_usterki": usterka_dla_wpisu,
                            "podmiot_odpowiedzialny": podmiot,
                            "link_do_zdjecia": ""
                        }
                        file_id_ze_zdjecia = wpis.get('file_id')
                        if file_id_ze_zdjecia:
                            link_zdjecia = f"https://drive.google.com/file/d/{file_id_ze_zdjecia}/view"
                            dane_json['link_do_zdjecia'] = link_zdjecia
                        
                        if zapisz_w_arkuszu(dane_json, message_time):
                            licznik_zapisanych += 1
                    
                    await update.message.reply_text(f"✅ Zakończono odbiór.\nZapisano {licznik_zapisanych} z {len(wpisy_lista)} usterek dla {identyfikator_odbioru}.",
                                                    reply_markup=START_KEYBOARD)
                
                chat_data.clear()
            else:
                await update.message.reply_text("Żaden odbiór nie jest aktywny.",
                                                reply_markup=START_KEYBOARD)
            return

        # SCENARIUSZ 3: Odbiór jest AKTYWNY, a to jest usterka TEKSTOWA
        if chat_data.get('odbiur_aktywny'):
            logger.info(f"Odbiór aktywny. Zapisywanie usterki tekstowej: '{user_message}'")
            
            usterka_opis_raw = user_message.strip()
            prefix_lokalu = chat_data.get('biezacy_lokal_w_szeregu')
            
            if not prefix_lokalu:
                await update.message.reply_text(
                    "❌ BŁĄD: Nie wybrano lokalu.\n\n"
                    "Proszę, <b>wybierz lokal z przycisków poniżej</b> i wpisz usterkę ponownie.",
                    reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                    parse_mode='HTML'
                )
                return

            # Połączenie lokal + tekst usterki
            usterka_opis = f"{prefix_lokalu} - {usterka_opis_raw}"
            
            usterka_id = str(uuid.uuid4())
            nowy_wpis = {
                'id': usterka_id,
                'typ': 'tekst',
                'opis': usterka_opis
            }
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            await update.message.reply_text(f"➕ Dodano: <b>{usterka_opis}</b>\n"
                                            f"(Łącznie: {len(chat_data['odbiur_wpisy'])}).",
                                            reply_markup=get_inline_keyboard(usterka_id=usterka_id, context=context),
                                            parse_mode='HTML')
            return

    except Exception as session_err:
        logger.error(f"Wystąpił nieoczekiwany błąd w logice sesji: {session_err}")
        await update.message.reply_text(f"❌ Wystąpił krytyczny błąd: {session_err}", reply_markup=START_KEYBOARD)
        return

    # --- FALLBACK ---
    if not chat_data.get('odbiur_aktywny'):
        logger.warning(f"Otrzymano nieobsługiwaną wiadomość poza sesją: {user_message}")
        await update.message.reply_text(
            "Nieprawidłowa komenda. Aby rozpocząć, naciśnij przycisk 'NOWY ODBIÓR'.",
            reply_markup=START_KEYBOARD
        )


# --- 7b. HANDLER DLA ZDJĘĆ ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Przechwytuje zdjęcie W TRAKCIE aktywnej sesji odbioru."""
    chat_data = context.chat_data
    
    if not chat_data.get('odbiur_aktywny'):
        await update.message.reply_text("Wyślij zdjęcie *po* rozpoczęciu odbioru. Teraz ta fotka zostanie zignorowana.",
                                        reply_markup=START_KEYBOARD)
        return

    usterka_opis_raw = update.message.caption
    if not usterka_opis_raw:
        await update.message.reply_text("❌ Zdjęcie musi mieć opis (usterkę)!",
                                        reply_markup=get_inline_keyboard(usterka_id=None, context=context))
        return

    podmiot = chat_data.get('odbiur_podmiot')
    tryb = chat_data.get('tryb_odbioru')
    
    prefix_lokalu = chat_data.get('biezacy_lokal_w_szeregu') # Np. "70/1"
    
    opis_do_nazwy_pliku = usterka_opis_raw.strip()
    
    target_folder_name = ""
    
    if prefix_lokalu:
        target_folder_name = prefix_lokalu.replace('/', '.')
        opis_do_arkusza = f"{prefix_lokalu} - {usterka_opis_raw} (zdjęcie)"
    else:
        await update.message.reply_text(
            "❌ BŁĄD: Nie wybrano lokalu dla zdjęcia.\n\n"
            "Proszę, <b>wybierz lokal z przycisków poniżej</b> i wyślij zdjęcie ponownie.",
            reply_markup=get_inline_keyboard(usterka_id=None, context=context),
            parse_mode='HTML'
        )
        return

    await update.message.reply_text(f"Otrzymano zdjęcie dla usterki: '{opis_do_nazwy_pliku}'.\n"
                                    f"Wysyłam do folderu: <b>{target_folder_name}</b>...",
                                    reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                                    parse_mode='HTML')

    try:
        photo_file = await update.message.photo[-1].get_file()
        file_bytes_io = io.BytesIO()
        await photo_file.download_to_memory(file_bytes_io)
        
        success, message, file_id = upload_photo_to_drive(
            file_bytes_io, 
            target_folder_name,
            opis_do_nazwy_pliku,
            podmiot, 
            tryb_odbioru=tryb
        )
        
        if success:
            usterka_id = str(uuid.uuid4())
            nowy_wpis = {
                'id': usterka_id,
                'typ': 'zdjecie',
                'opis': opis_do_arkusza, 
                'file_id': file_id
            }
            chat_data['odbiur_wpisy'].append(nowy_wpis)
            
            await update.message.reply_text(f"✅ Zdjęcie zapisane na Drive jako: <b>{message}</b>\n"
                                            f"➕ Usterka dodana do listy: <b>{opis_do_arkusza}</b>\n"
                                            f"(Łącznie: {len(chat_data['odbiur_wpisy'])}).",
                                            reply_markup=get_inline_keyboard(usterka_id=usterka_id, context=context),
                                            parse_mode='HTML')
        else:
            await update.message.reply_text(f"❌ Błąd Google Drive: {message}",
                                            reply_markup=get_inline_keyboard(usterka_id=None, context=context))
            
    except Exception as e:
        logger.error(f"Błąd podczas przetwarzania zdjęcia: {e}")
        await update.message.reply_text(f"❌ Wystąpił błąd przy pobieraniu zdjęcia: {e}",
                                        reply_markup=get_inline_keyboard(usterka_id=None, context=context))


# --- 7c. HANDLER: Obsługa przycisków Inline ---
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obsługuje naciśnięcia przycisków inline."""
    query = update.callback_query
    await query.answer() 
    
    chat_data = context.chat_data
    data = query.data

    # --- Logika dla 'start_menu' (przycisk "Anuluj") ---
    if data == 'start_menu':
        if chat_data.get('odbiur_aktywny'):
            await query.answer("Odbiór jest aktywny. Zakończ go.", show_alert=True)
            return
        
        chat_data.clear()
        try:
            await query.edit_message_text("Anulowano wybór.", reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text("Gotowy na nowy odbiór.", reply_markup=START_KEYBOARD)
        return

    # --- Logika dla 'szereg_' ---
    elif data.startswith('szereg_'):
        if chat_data.get('odbiur_aktywny'):
            await query.answer("Odbiór jest aktywny. Zakończ go.", show_alert=True)
            return
        
        szereg_name = data.split('_', 1)[1]
        chat_data['wybrany_szereg'] = szereg_name
        
        chat_data['state'] = 'AWAITING_FIRMA_SZEREG'
        await query.edit_message_text(
            f"Wybrano: <b>CAŁY {szereg_name}</b>\n\n"
            f"Proszę, <b>podaj teraz nazwę firmy</b> wykonawczej (możesz wpisać skrót, np. 'Pelc' lub 'Ivan'):",
            parse_mode='HTML'
        )
        return
    
    # --- NOWA LOGIKA: Ustawianie aktywnego lokalu w trybie "Całe Szeregi" ---
    elif data.startswith("setlokal_"):
        if not chat_data.get('odbiur_aktywny'):
            await query.message.reply_text("Sesja nieaktywna.", reply_markup=START_KEYBOARD)
            return
        
        lokal_name = data.split('_', 1)[1]
        chat_data['biezacy_lokal_w_szeregu'] = lokal_name
        
        await query.answer(f"OK! Następne usterki będą dla lokalu: {lokal_name}")
        
        try:
            nowy_tekst = f"Aktywny lokal dla usterek: <b>{lokal_name}</b>\n(Ostatnia akcja: {datetime.now().strftime('%H:%M:%S')})"
            await query.edit_message_text(
                nowy_tekst,
                reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                parse_mode='HTML'
            )
        except Exception as e:
             logger.warning(f"Nie można edytować wiadomości po setlokal: {e}")
             await query.message.reply_text(f"Aktywny lokal dla usterek zmieniony na: <b>{lokal_name}</b>", 
                                            parse_mode='HTML',
                                            reply_markup=get_inline_keyboard(usterka_id=None, context=context))
        return
    
    # --- Logika dla 'cofnij' z ID ---
    elif data.startswith('cofnij_'):
        logger.info("Otrzymano callback 'cofnij' z ID")
        if not chat_data.get('odbiur_aktywny'):
            await query.message.reply_text("Sesja już się zakończyła.", reply_markup=START_KEYBOARD)
            return

        try:
            id_to_delete = data.split('_', 1)[1]
        except Exception as e:
            logger.error(f"Nie można sparsować ID z callback: {data} - {e}")
            await query.answer("Błąd: Nieprawidłowy format ID.", show_alert=True)
            return

        wpisy_lista = chat_data.get('odbiur_wpisy', [])
        wpis_to_delete = None
        
        for wpis in wpisy_lista:
            if wpis.get('id') == id_to_delete:
                wpis_to_delete = wpis
                break

        if not wpis_to_delete:
            logger.warning(f"Próbowano usunąć usterkę {id_to_delete}, ale już nie istnieje.")
            await query.answer("Ta usterka została już usunięta.", show_alert=True)
            try:
                await query.edit_message_text(f"--- TA USTERKA ZOSTAŁA JUŻ USUNIĘTA ---", reply_markup=None)
            except Exception:
                pass
            return

        try:
            opis_usunietego = wpis_to_delete.get('opis', 'NIEZNANY WPIS')
            wpisy_lista.remove(wpis_to_delete)
            chat_data['odbiur_wpisy'] = wpisy_lista
            
            delete_feedback = f"↩️ Usunięto: <b>{opis_usunietego}</b>"

            if wpis_to_delete.get('typ') == 'zdjecie':
                file_id_to_delete = wpis_to_delete.get('file_id')
                if file_id_to_delete:
                    delete_success, delete_error = delete_file_from_drive(file_id_to_delete)
                    if delete_success:
                        delete_feedback += "\n(Pomyślnie usunięto z Google Drive)."
                    else:
                        delete_feedback += f"\n(BŁĄD usuwania z Drive: {delete_error})."
            
            try:
                await query.edit_message_text(f"--- USUNIĘTO: <b>{opis_usunietego}</b> ---", reply_markup=None, parse_mode='HTML')
            except Exception:
                pass

            await query.message.reply_text(f"{delete_feedback}\n(Pozostało: {len(wpisy_lista)}).", 
                                           reply_markup=get_inline_keyboard(usterka_id=None, context=context),
                                           parse_mode='HTML')
        
        except Exception as e:
            logger.error(f"Błąd podczas usuwania wpisu: {e}")
            await query.answer(f"Błąd: {e}", show_alert=True)

    # --- Logika dla 'koniec_odbioru' ---
    elif data == 'koniec_odbioru':
        logger.info("Otrzymano callback 'koniec_odbioru'")
        if not chat_data.get('odbiur_aktywny'):
            await query.message.reply_text("Żaden odbiór nie jest aktywny.", reply_markup=START_KEYBOARD)
            return
        
        identyfikator_odbioru = chat_data.get('odbiur_identyfikator', 'Brak ID Odbioru')
        podmiot = chat_data.get('odbiur_podmiot')
        wpisy_lista = chat_data.get('odbiur_wpisy', [])
        message_time = datetime.now() 
        
        if not wpisy_lista:
            await query.message.reply_text(f"Zakończono odbiór dla {identyfikator_odbioru}. Nie dodano żadnych usterek.",
                                           reply_markup=START_KEYBOARD)
        else:
            logger.info(f"Zapisywanie {len(wpisy_lista)} usterek dla {identyfikator_odbioru}...")
            licznik_zapisanych = 0
            
            for wpis in wpisy_lista:
                opis_caly = wpis.get('opis', 'BŁĄD WPISU')
                
                lokal_dla_wpisu = identyfikator_odbioru
                usterka_dla_wpisu = opis_caly

                if ' - ' in opis_caly:
                    parts = opis_caly.split(' - ', 1)
                    if len(parts) == 2:
                        lokal_dla_wpisu = parts[0]
                        usterka_dla_wpisu = parts[1]
                
                dane_json = {
                    "numer_lokalu_budynku": lokal_dla_wpisu,
                    "rodzaj_usterki": usterka_dla_wpisu,
                    "podmiot_odpowiedzialny": podmiot,
                    "link_do_zdjecia": ""
                }
                file_id_ze_zdjecia = wpis.get('file_id')
                if file_id_ze_zdjecia:
                    link_zdjecia = f"https://drive.google.com/file/d/{file_id_ze_zdjecia}/view"
                    dane_json['link_do_zdjecia'] = link_zdjecia
                
                if zapisz_w_arkuszu(dane_json, message_time):
                    licznik_zapisanych += 1
            
            await query.message.reply_text(f"✅ Zakończono odbiór.\nZapisano {licznik_zapisanych} z {len(wpisy_lista)} usterek dla {identyfikator_odbioru}.",
                                           reply_markup=START_KEYBOARD)
        
        chat_data.clear()
        
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.warning(f"Nie można edytować starej wiadomości: {e}")
            
    # --- Logika dla pustego przycisku (np. separator) ---
    elif data == "noop":
        await query.answer() 
        return


# --- 8. Uruchomienie Bota ---
def main():
    """Główna funkcja uruchamiająca bota dla hostingu."""
    
    logger.info("Uruchamianie bota w trybie WEBHOOK...")
    
    PORT = int(os.environ.get('PORT', 8443))
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN')
    
    if domain:
        WEBHOOK_URL = f"https://{domain}"
        logger.info(f"Wykryto domenę Railway: {WEBHOOK_URL}")
    else:
        WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
        if not WEBHOOK_URL:
            logger.critical("BŁĄD: Nie znaleziono zmiennej RAILWAY_PUBLIC_DOMAIN ani WEBHOOK_URL!")
            exit()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    logger.info(f"Ustawianie webhooka na: {WEBHOOK_URL}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    )
    logger.info(f"Bot nasłuchuje na porcie {PORT}")

if __name__ == '__main__':
    main()
