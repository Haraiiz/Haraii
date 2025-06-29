import logging
import sys
import os # Import modul os, meskipun sebagian besar Railway-specific logic dihapus, tetap ada untuk kompatibilitas jika diperlukan di masa depan.
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ChatMemberHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    PicklePersistence,
)
from telegram.error import BadRequest, Forbidden

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# --- KONFIGURASI ---
# Mengambil BOT_TOKEN dari environment variable di Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("FATAL ERROR: BOT_TOKEN tidak ditemukan di environment variables.")
    sys.exit("BOT_TOKEN tidak diatur!") 

# ID dan link channel wajib untuk verifikasi pengguna di chat pribadi bot
REQUIRED_CHANNEL_ID = -1002634779221
REQUIRED_CHANNEL_LINK = "https://t.me/Privateyyds"

# URL Gambar dari Imgur untuk tampilan bot yang lebih menarik
IMAGE_URL_VERIFICATION = "https://i.imgur.com/JS49Nau.jpeg" # Gambar untuk menu verifikasi/selamat datang
IMAGE_URL_MAIN_MENU = "https://i.imgur.com/T1r2fbC.jpeg" # Gambar untuk menu utama pribadi & grup

# States untuk ConversationHandler dalam alur pengaturan channel pribadi
GET_CHANNEL_ID = range(1)

# --- FUNGSI PEMERIKSAAN IZIN (UNIVERSAL UNTUK CHAT/GROUP) ---

async def check_bot_ban_permissions(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> (bool, str):
    """
    Memeriksa apakah bot adalah admin dengan izin 'Ban Users' di chat (channel atau group) yang diberikan.
    Mengembalikan (True, "Success") jika valid, atau (False, "Pesan Error") jika tidak.
    """
    try:
        # Mendapatkan objek member bot di chat_id yang diberikan
        bot_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=context.bot.id)
        
        # Memeriksa apakah status bot adalah administrator
        if bot_member.status != "administrator":
            return False, "Bot bukan admin di chat tersebut. Mohon jadikan bot admin terlebih dahulu."
        
        # Memeriksa apakah bot memiliki izin untuk 'Ban Users'
        if not bot_member.can_restrict_members:
            return False, "Bot adalah admin, tetapi tidak memiliki izin untuk 'Ban Users'. Mohon berikan izin tersebut."
        
        return True, "Bot adalah admin dengan izin yang benar."
    except (BadRequest, Forbidden) as e:
        logger.error(f"Error checking permissions for chat {chat_id}: {e}")
        # Menangani error spesifik jika bot tidak ditemukan di chat atau chat tidak ditemukan
        if "user not found" in str(e) or "chat not found" in str(e):
             return False, "Bot tidak ditemukan di chat tersebut. Mohon tambahkan bot terlebih dahulu."
        return False, f"Terjadi kesalahan: {e}" # Mengembalikan pesan error yang lebih umum jika terjadi masalah lain
    except Exception as e:
        logger.error(f"Unexpected error checking permissions for chat {chat_id}: {e}")
        return False, "Terjadi kesalahan tak terduga saat memeriksa izin."

# --- Helper untuk kirim/edit pesan foto ---
async def send_or_edit_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE, photo_url: str, caption_text: str, reply_markup: InlineKeyboardMarkup, is_new_message: bool = False):
    """
    Mengirim pesan foto baru atau mengedit caption dari pesan foto yang sudah ada.
    - is_new_message: True jika ini adalah pesan baru (misal dari /start atau setelah delete pesan lama).
    - caption_text: Teks singkat untuk caption foto, karena Telegram punya batasan panjang caption.
    """
    target_chat_id = update.effective_chat.id

    if is_new_message:
        # Mengirim pesan foto baru
        sent_message = await context.bot.send_photo(
            chat_id=target_chat_id,
            photo=photo_url,
            caption=caption_text, # Caption singkat di sini
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return sent_message.message_id
    elif update.callback_query:
        query = update.callback_query
        try:
            # Mengedit caption dari pesan yang memicu callback query
            # Metode edit_caption() dari query.message tidak memerlukan chat_id atau message_id eksplisit
            await query.message.edit_caption(
                caption=caption_text, # Caption singkat di sini
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return query.message.message_id # Mengembalikan ID pesan yang baru saja diedit
        except BadRequest as e:
            logger.warning(f"Gagal mengedit caption pesan via callback di chat {target_chat_id}: {e}. Mengirim pesan baru sebagai fallback.")
            # Fallback: jika gagal edit (misal karena caption terlalu panjang di update sebelumnya atau pesan sudah tidak ada), kirim pesan baru
            sent_message = await context.bot.send_photo(
                chat_id=target_chat_id,
                photo=photo_url,
                caption=caption_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return sent_message.message_id
    else:
        # Ini adalah kasus fallback jika bukan pesan baru dan bukan dari callback query.
        # Biasanya terjadi jika ada interaksi langsung atau pesan bot yang tidak dilacak ID-nya.
        # Paling aman adalah mengirim pesan baru.
        sent_message = await context.bot.send_photo(
            chat_id=target_chat_id,
            photo=photo_url,
            caption=caption_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return sent_message.message_id

# --- MENU UTAMA & NAVIGASI (UNTUK CHAT PRIBADI) ---

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text="", is_initial_load=False):
    """
    Menampilkan menu utama dengan tombol interaktif untuk pengaturan channel pribadi pengguna.
    Menggunakan pesan foto dengan caption singkat dan tombol.
    """
    user_data = context.user_data
    banning_status = user_data.get('banning_enabled', False)
    toggle_text = "🔴 Matikan Ban (Channel)" if banning_status else "🟢 Aktifkan Ban (Channel)"
    monitored_channel = user_data.get('monitored_channel_title', 'Belum Diatur')

    # Caption singkat untuk foto menu utama
    caption = (
        f"🏠 **Menu Utama (Pengelolaan Channel Pribadi)**\n\n"
        f"▪️ **Channel Target**: `{monitored_channel}`\n"
        f"▪️ **Status Banning**: `{'Aktif' if banning_status else 'Tidak Aktif'}`"
    )

    keyboard = [
        [InlineKeyboardButton("➕ Kelola Channel Target", callback_data="set_channel")],
        [InlineKeyboardButton(toggle_text, callback_data="toggle_channel_ban")],
        [InlineKeyboardButton("📖 Cara Pakai (Wajib Baca!)", callback_data="how_to_use_channel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    current_message_id = user_data.get('last_private_menu_message_id')

    # Logika untuk mengirim pesan foto baru atau mengedit yang sudah ada
    if is_initial_load or (update.message and not update.callback_query):
        # Jika ini adalah load awal atau dari command /start atau /cancel,
        # hapus pesan lama (jika ada) dan kirim pesan foto baru.
        if current_message_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=current_message_id)
            except Exception as e:
                logger.warning(f"Gagal menghapus pesan lama (ID: {current_message_id}) di chat pribadi: {e}")
        new_message_id = await send_or_edit_photo_message(
            update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
            is_new_message=True
        )
    elif update.callback_query:
        # Jika dari callback query (misal klik tombol), edit pesan yang sama.
        await update.callback_query.answer()
        new_message_id = await send_or_edit_photo_message(
            update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
            is_new_message=False
        )
    else: # Fallback case, seharusnya jarang terjadi jika alur logikanya benar
        new_message_id = await send_or_edit_photo_message(
            update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
            is_new_message=True
        )
        logger.warning(f"show_main_menu: Tipe update tidak ditangani. Mengirim pesan baru.")

    # Simpan message_id dari pesan menu yang baru dikirim/diedit
    user_data['last_private_menu_message_id'] = new_message_id

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kembali ke menu utama dari menu lain (untuk chat pribadi)."""
    await show_main_menu(update, context)

# --- MENU & NAVIGASI (UNTUK GRUP) ---

async def show_group_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text=""):
    """
    Menampilkan menu untuk pengaturan bot di dalam grup.
    Menggunakan pesan foto dengan caption singkat dan tombol.
    """
    chat_id = update.effective_chat.id
    group_data = context.chat_data # Mengakses data khusus untuk grup ini

    banning_status = group_data.get('banning_enabled', False)
    toggle_text = "🔴 Matikan Ban (Group)" if banning_status else "🟢 Aktifkan Ban (Group)"

    # Caption singkat untuk foto menu grup
    caption = (
        f"🏠 **Menu Bot (Group)**\n\n"
        f"Selamat datang di group `{update.effective_chat.title}`!\n"
        f"▪️ **Status Banning**: `{'Aktif' if banning_status else 'Tidak Aktif'}`"
    )

    keyboard = [
        [InlineKeyboardButton(toggle_text, callback_data="toggle_group_ban")],
        [InlineKeyboardButton("📖 Cara Pakai Group", callback_data="how_to_use_group")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    current_message_id = group_data.get('last_group_menu_message_id')

    if update.message and not update.callback_query: # Berasal dari command /start atau pesan biasa
        if current_message_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=current_message_id)
            except Exception as e:
                logger.warning(f"Gagal menghapus pesan lama group (ID: {current_message_id}): {e}")
        new_message_id = await send_or_edit_photo_message(
            update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
            is_new_message=True
        )
    elif update.callback_query:
        await update.callback_query.answer()
        new_message_id = await send_or_edit_photo_message(
            update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
            is_new_message=False
        )
    else: # Fallback
        new_message_id = await send_or_edit_photo_message(
            update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
            is_new_message=True
        )
        logger.warning(f"show_group_menu: Tipe update tidak ditangani. Mengirim pesan baru.")

    group_data['last_group_menu_message_id'] = new_message_id

async def back_to_group_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kembali ke menu group dari menu lain."""
    await show_group_menu(update, context)

# --- ALUR VERIFIKASI PENGGUNA BARU (HANYA UNTUK CHAT PRIBADI) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fungsi /start, memeriksa apakah pengguna sudah terverifikasi (untuk chat pribadi)
    atau menampilkan menu group (untuk chat grup).
    """
    chat_type = update.effective_chat.type
    user_id = update.effective_user.id

    if chat_type == Chat.PRIVATE:
        # Hapus pesan /start yang dikirim pengguna untuk menjaga kebersihan chat
        if update.message:
            try:
                await update.message.delete()
            except Exception as e:
                logger.warning(f"Gagal menghapus pesan /start dari user: {e}")

        try:
            member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator']:
                context.user_data['is_verified'] = True
                await show_main_menu(update, context, is_initial_load=True) # Mengirim pesan foto menu utama
            else:
                raise ValueError("User not in channel")
        except Exception:
            context.user_data['is_verified'] = False
            keyboard = [[InlineKeyboardButton("✅ Saya Sudah Bergabung", callback_data="verify_join")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Caption singkat untuk foto verifikasi
            caption = f"**Selamat Datang!**\n\nUntuk mengaktifkan fitur, **wajib** gabung channel kami dulu ya. Klik tombol!"
            
            new_message_id = await send_or_edit_photo_message(
                update, context, IMAGE_URL_VERIFICATION,
                caption, # Gunakan caption singkat di sini
                reply_markup, is_new_message=True
            )
            context.user_data['last_private_menu_message_id'] = new_message_id

            # Kirim detail verifikasi sebagai pesan teks terpisah
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"➡️ **Join di sini**: {REQUIRED_CHANNEL_LINK}\n\nSetelah bergabung, klik tombol di atas untuk verifikasi.",
                parse_mode='Markdown',
                disable_web_page_preview=True # Untuk menghindari preview link jika tidak diinginkan
            )

    elif chat_type in [Chat.GROUP, Chat.SUPERGROUP]:
        # Hapus pesan /start yang dikirim pengguna di grup
        if update.message:
            try:
                await update.message.delete()
            except Exception as e:
                logger.warning(f"Gagal menghapus pesan /start dari user di grup: {e}")
        await show_group_menu(update, context)

async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memverifikasi ulang keanggotaan pengguna setelah mereka menekan tombol (untuk chat pribadi)."""
    query = update.callback_query
    user_id = query.from_user.id
    current_message_id = context.user_data.get('last_private_menu_message_id')

    try:
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            context.user_data['is_verified'] = True
            await query.answer("✅ Verifikasi berhasil!", show_alert=True)
            # Hapus pesan verifikasi lama (termasuk foto) dan kirim pesan menu utama baru
            if current_message_id:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=current_message_id)
                except Exception as e:
                    logger.warning(f"Gagal menghapus pesan verifikasi lama (ID: {current_message_id}): {e}")
            await show_main_menu(update, context, message_text="Verifikasi berhasil! Selamat datang di Menu Utama.", is_initial_load=True)
        else:
            raise ValueError("User not in channel")
    except Exception:
        await query.answer("❌ Anda belum bergabung. Silakan join channel terlebih dahulu.", show_alert=True)

# --- HANDLER UNTUK FITUR-FITUR BOT (PENGATURAN CHANNEL PRIBADI) ---

async def start_set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Memulai proses pengaturan channel target (untuk chat pribadi)."""
    query = update.callback_query
    await query.answer()
    
    # Caption singkat untuk prompt input channel
    caption = "✍️ **Kirimkan Username atau ID Channel Anda**\n\nPastikan bot ini sudah jadi **Admin** dengan izin **'Ban Users'** ya!"

    # Edit pesan foto menu utama yang ada dengan caption prompt
    await send_or_edit_photo_message(
        update, context, IMAGE_URL_MAIN_MENU, caption, InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Batalkan", callback_data="back_to_main")]]),
        is_new_message=False
    )
    return GET_CHANNEL_ID # Mengembalikan GET_CHANNEL_ID agar ConversationHandler menunggu input

async def get_channel_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menerima, memvalidasi channel, dan memeriksa izin bot (untuk chat pribadi)."""
    channel_input = update.message.text
    if update.message:
        try:
            await update.message.delete() # Hapus pesan input dari pengguna
        except Exception as e:
            logger.warning(f"Gagal menghapus pesan input channel: {e}")

    feedback_text = "" # Inisialisasi feedback_text untuk pesan teks terpisah
    
    try:
        chat = await context.bot.get_chat(chat_id=channel_input)
        is_valid, message = await check_bot_ban_permissions(context, chat.id)
        
        if is_valid:
            context.user_data['monitored_channel_id'] = chat.id
            context.user_data['monitored_channel_title'] = chat.title
            context.user_data['banning_enabled'] = False 
            feedback_text = f"✅ **Berhasil!**\nChannel **{chat.title}** telah ditambahkan. Silakan aktifkan fitur ban dari Menu Utama."
        else:
            feedback_text = f"❌ **Gagal!**\n{message}\n\nMohon perbaiki dan coba lagi."
            
    except (BadRequest, Forbidden) as e:
        logger.error(f"Gagal mendapatkan info channel {channel_input}: {e}")
        feedback_text = "❌ **Gagal!**\nChannel dengan username/ID tersebut tidak ditemukan atau bot tidak memiliki akses."
    
    # Kirim feedback sebagai pesan teks biasa
    await update.effective_chat.send_message(text=feedback_text, parse_mode='Markdown')

    # Kembali ke main menu, yang akan memperbarui tampilan menu utama
    await show_main_menu(update, context, is_initial_load=True) # Mengirim pesan foto menu utama yang baru
    return ConversationHandler.END

async def toggle_channel_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengaktifkan atau menonaktifkan fitur banning untuk channel (untuk chat pribadi)."""
    query = update.callback_query
    channel_id = context.user_data.get('monitored_channel_id')

    if not channel_id:
        await query.answer("⚠️ Anda harus mengatur Channel Target terlebih dahulu!", show_alert=True)
        return

    if not context.user_data.get('banning_enabled', False):
        is_valid, message = await check_bot_ban_permissions(context, channel_id)
        if not is_valid:
            context.user_data['banning_enabled'] = False
            await query.answer(f"Gagal Mengaktifkan: {message}", show_alert=True)
            await show_main_menu(update, context) # Kembali ke menu utama dengan status terbaru
            return

    current_state = context.user_data.get('banning_enabled', False)
    new_state = not current_state
    context.user_data['banning_enabled'] = new_state
    
    status_text = "Aktif" if new_state else "Tidak Aktif"
    await query.answer(f"Fitur Banning Channel sekarang: {status_text}", show_alert=True)
    await show_main_menu(update, context)

async def how_to_use_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan panduan penggunaan bot untuk channel (untuk chat pribadi)."""
    query = update.callback_query
    await query.answer()
    
    # Caption singkat untuk gambar "Cara Pakai"
    caption = "📖 **Panduan Penggunaan Bot (Channel)**"
    keyboard = [[InlineKeyboardButton("⬅️ Balik ke Menu Utama", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Edit pesan gambar yang ada dengan caption singkat dan tombol
    await send_or_edit_photo_message(
        update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
        is_new_message=False
    )

    # Kirim detail panduan sebagai pesan teks terpisah (boleh panjang)
    detailed_text = (
        "Halo, bestie! Bot ini tuh fungsinya simpel tapi nampol: **nge-ban otomatis** member yang *left* dari channel Telegram kesayangan kamu. Biar channel kamu isinya member loyal semua!\n\n"
        "--- \n\n"
        "✨ **STEP 1: Nambahin Channel Target**\n\n"
        "Ini dia langkah awal buat botnya kenal sama channel kamu:\n"
        "1.  Dari **Menu Utama** bot (yang lagi kita diemin ini), klik tombol `➕ Kelola Channel Target`.\n"
        "2.  Nanti bot bakal minta kamu kirimin **username** atau **ID** channel. Nah, ini penting nih:\n"
        "    •   **Buat Channel Publik (yang ada `@` di namanya)**:\n"
        "        Gampang banget! Tinggal copy aja username lengkapnya, misalnya: `@contohchannelpublic`. Terus langsung kirim ke bot ini.\n"
        "    •   **Buat Channel Privat (yang link-nya biasanya cuma buat invite)**:\n"
        "        Ini agak butuh effort dikit, bestie. Kamu harus dapetin ID numeriknya yang panjang (`-100` blablabla).\n"
        "        Caranya? Gini nih:\n"
        "        a.  Add bot lain kayak `@userinfobot` atau sejenisnya ke channel privat kamu.\n"
        "        b.  Bot tersebut akan menampilkan ID channel (biasanya diawali dengan `-100`).\n"
        "        c.  Nah, salin ID itu, terus kirim ke bot ini.\n\n"
        "🚨 **PENTING BANGET, WAJIB BACA!** 🚨\n"
        "Sebelum kamu kirim ID/username channel, **PASTIKAN bot ini udah kamu jadiin ADMIN di channel kamu itu!** Plus, bot ini **WAJIB punya izin 'Ban Users'**. Kalau nggak, botnya nggak bakal bisa nambahin channel kamu jadi target, apalagi nge-ban. Jadi, pastiin izinnya on ya! Botnya bakal auto-cek kok, jadi kalau ada yang kurang, dia bakal ngasih tahu.\n\n"
        "--- \n\n"
        "🚀 **STEP 2: Aktifin Fitur Banning**\n\n"
        "Channel udah kenal? Saatnya bikin botnya kerja:\n"
        "1.  Setelah channel target berhasil di-setting, balik lagi ke **Menu Utama**.\n"
        "2.  Kamu bakal liat tombol `🟢 Aktifkan Ban (Channel)`. Pencet aja tombol itu!\n"
        "3.  Kalau berhasil, tombolnya bakal berubah jadi `🔴 Matikan Ban (Channel)`. Artinya fitur ban udah ON! Best!\n"
        "4.  **FYI:** Kalau pas ngaktifin ini muncul error, itu tandanya izin admin bot di channel kamu dicabut atau ada yang salah. Pastiin izinnya dibalikin lagi ya, terus coba lagi.\n"
        "5.  Dan voilà! Mulai sekarang, setiap ada member yang *berani* left dari channel target kamu, mereka bakal langsung di-ban permanen! Auto-bersih!\n\n"
        "--- \n\n"
        "🔔 **Notifikasi Ban (Private Chat)**\n\n"
        "Setiap kali bot ini sukses nge-ban seseorang dari channel kamu, kamu bakal dapet notifikasi langsung di chat pribadi ini. Jadi, kamu selalu tahu siapa aja yang nggak loyal, hehe."
    )
    await update.effective_chat.send_message(text=detailed_text, parse_mode='Markdown', disable_web_page_preview=True)

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Membatalkan aksi saat ini dan kembali ke menu utama (untuk chat pribadi)."""
    if update.message:
        try:
            await update.message.delete() # Hapus pesan /cancel yang dikirim pengguna
        except Exception as e:
            logger.warning(f"Gagal menghapus pesan /cancel: {e}")

    await show_main_menu(update, context, message_text="Aksi dibatalkan. Kembali ke Menu Utama.", is_initial_load=True)
    return ConversationHandler.END

# --- HANDLER UNTUK FITUR-FITUR BOT (PENGATURAN GRUP) ---

async def toggle_group_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengaktifkan atau menonaktifkan fitur banning untuk grup (dari dalam grup)."""
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    chat_title = query.message.chat.title

    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member.status not in ["administrator", "creator"]:
            await query.answer("❌ Hanya admin group yang bisa mengaktifkan atau menonaktifkan fitur ini.", show_alert=True)
            return
    except Exception as e:
        logger.error(f"Error checking user permissions in group {chat_id}: {e}")
        await query.answer("Terjadi kesalahan saat memeriksa izin Anda.", show_alert=True)
        return

    is_valid, message = await check_bot_ban_permissions(context, chat_id)

    if not context.chat_data.get('banning_enabled', False) and not is_valid:
        context.chat_data['banning_enabled'] = False
        await query.answer(f"Gagal Mengaktifkan: {message}", show_alert=True)
        await show_group_menu(update, context, message_text=f"❌ **Gagal!**\n{message}")
        return

    current_state = context.chat_data.get('banning_enabled', False)
    new_state = not current_state
    context.chat_data['banning_enabled'] = new_state

    status_text = "Aktif" if new_state else "Tidak Aktif"
    await query.answer(f"Fitur Banning Group sekarang: {status_text}", show_alert=True)
    await show_group_menu(update, context)

async def how_to_use_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan panduan penggunaan bot untuk grup (dari dalam grup)."""
    query = update.callback_query
    await query.answer()

    # Caption singkat untuk gambar "Cara Pakai"
    caption = "📖 **Panduan Penggunaan Bot (Group)**"
    keyboard = [[InlineKeyboardButton("⬅️ Balik ke Menu Group", callback_data="back_to_group_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Edit pesan gambar yang ada dengan caption singkat dan tombol
    await send_or_edit_photo_message(
        update, context, IMAGE_URL_MAIN_MENU, caption, reply_markup,
        is_new_message=False
    )

    # Kirim detail panduan sebagai pesan teks terpisah (boleh panjang)
    detailed_text = (
        "Woy bestie! Bot ini tuh auto-ban member yang kabur dari grup kamu. Jadi, grup kamu bakal aman dari *silent left* yang bikin gabut.\n\n"
        "--- \n\n"
        "✨ **STEP 1: Masukin Bot ke Group Kamu**\n\n"
        "Pertama-tama, biar botnya bisa kerja di grup kamu, wajib banget:\n"
        "1.  **Add bot ini ke group kamu!** Gampang, kayak nambahin member biasa.\n"
        "2.  Ini yang paling penting: **JADIKAN BOT INI ADMIN di grup kamu!** Dan jangan lupa, botnya **WAJIB punya izin 'Ban Users'** (atau 'Restrict Members', yang penting bisa nge-kick/ban orang). Tanpa izin ini, botnya cuma jadi pajangan, nggak bisa kerja apa-apa.\n\n"
        "--- \n\n"
        "🚀 **STEP 2: Aktifin Fitur Banning di Group**\n\n"
        "Kalau botnya udah jadi admin, lanjut ke sini:\n"
        "1.  Ketik `/start` di dalam group ini. Nanti botnya bakal munculin menu khusus grup.\n"
        "2.  Klik tombol `🟢 Aktifkan Ban (Group)`. Kalau udah aktif, tombolnya berubah jadi `🔴 Matikan Ban (Group)`.\n"
        "3.  **FYI:** Fitur ini **hanya bisa diaktifkan atau dimatikan oleh admin grup** ya. Jadi, kalau kamu bukan admin, tombolnya nggak akan berfungsi.\n"
        "4.  Botnya bakal auto-cek lagi izinnya pas kamu mau aktifin. Kalau izin 'Ban Users'-nya nggak ada, ya nggak bisa diaktifin, bestie.\n\n"
        "--- \n\n"
        "🎮 **Cara Kerja Botnya (Simple Banget!)**\n\n"
        "Setelah fitur ban aktif, gini nih cara kerjanya:\n"
        "•   Setiap ada member yang keluar dari grup ini, bot bakal otomatis mencoba nge-ban mereka secara permanen.\n"
        "•   Pokoknya, kalau ada yang *left*, langsung **auto-ban** biar kapok! 😂\n\n"
        "--- \n\n"
        "🔔 **Notifikasi Ban (Langsung di Group)**\n\n"
        "Setiap kali bot berhasil nge-ban seseorang dari grup, notifikasinya bakal muncul langsung di chat grup ini. Jadi, semua member (dan kamu sebagai admin) bisa langsung tahu siapa yang di-ban. Keren kan?"
    )
    await update.effective_chat.send_message(text=detailed_text, parse_mode='Markdown', disable_web_page_preview=True)


# --- FUNGSI UTAMA UNTUK MEMPROSES UPDATE ANGGOTA (DETEKSI USER KELUAR) ---

async def handle_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Memproses update status anggota dan melakukan ban jika sesuai, baik untuk channel maupun group.
    Fungsi ini akan mendeteksi ketika seorang pengguna meninggalkan chat (channel atau group)
    dan mencoba memblokirnya jika fitur banning aktif.
    """
    if not update.chat_member: # Pastikan ini adalah update chat_member
        return
    
    leaving_user = update.chat_member.new_chat_member.user
    chat_id_of_event = update.chat_member.chat.id # ID chat tempat event terjadi (bisa Channel atau Group)
    chat_title_of_event = update.chat_member.chat.title # Nama chat tempat event terjadi

    # Hanya proses jika status lama adalah 'member' atau 'restricted' dan status baru adalah 'left'
    if not (update.chat_member.old_chat_member.status in ["member", "restricted"] and
            update.chat_member.new_chat_member.status == "left"):
        return # Abaikan jika bukan event meninggalkan chat

    banned_successfully = False # Flag untuk menghindari double-banning/notifikasi

    # 1. Cek apakah event ini terjadi di CHANNEL yang dimonitor oleh salah satu pengguna bot
    # Iterasi melalui semua data pengguna yang disimpan oleh bot
    for user_id_owner, user_data_owner in context.application.user_data.items():
        # Jika channel yang dimonitor pengguna cocok dengan chat_id_of_event DAN fitur banning aktif untuk channel tersebut
        if user_data_owner.get('monitored_channel_id') == chat_id_of_event and user_data_owner.get('banning_enabled', False):
            try:
                # Coba ban pengguna dari channel
                await context.bot.ban_chat_member(chat_id=chat_id_of_event, user_id=leaving_user.id)
                logger.info(f"Berhasil memblokir {leaving_user.full_name} dari channel {chat_id_of_event} milik user {user_id_owner} (via pengaturan pribadi)")
                
                # Kirim notifikasi sukses ke chat pribadi pemilik bot
                await context.bot.send_message(
                    chat_id=user_id_owner,
                    text=f"✅ **Notifikasi Blokir (Channel)**\n\nPengguna berikut telah keluar dari channel **{chat_title_of_event}** dan berhasil diblokir:\n\n▪️ **Nama**: {leaving_user.full_name}\n▪️ **Username**: @{leaving_user.username or 'Tidak ada'}\n▪️ **ID**: `{leaving_user.id}`",
                    parse_mode='Markdown'
                )
                banned_successfully = True
                break # Setelah menemukan channel yang cocok dan berhasil di-ban, berhenti iterasi
            except Exception as e:
                logger.error(f"Gagal memblokir {leaving_user.id} di channel {chat_id_of_event} (pengaturan pribadi): {e}")
                # Kirim notifikasi gagal ke chat pribadi pemilik bot
                await context.bot.send_message(
                    chat_id=user_id_owner,
                    text=f"❌ **Gagal Memblokir (Channel)**\n\nGagal memblokir {leaving_user.full_name} di channel **{chat_title_of_event}**.\n**Error**: `{e}`\n\nPastikan bot masih menjadi admin dengan izin ban."
                )

    # 2. Cek apakah event ini terjadi di GROUP tempat bot diaktifkan
    # context.application.chat_data menyimpan data untuk setiap chat (group)
    group_specific_data = context.application.chat_data.get(chat_id_of_event)
    if group_specific_data and group_specific_data.get('banning_enabled', False):
        if not banned_successfully: # Hanya proses jika belum di-ban oleh logika channel
            try:
                # Coba ban pengguna dari grup
                await context.bot.ban_chat_member(chat_id=chat_id_of_event, user_id=leaving_user.id)
                logger.info(f"Berhasil memblokir {leaving_user.full_name} dari group {chat_id_of_event} (via pengaturan group)")
                
                # Kirim notifikasi sukses ke grup itu sendiri
                await context.bot.send_message(
                    chat_id=chat_id_of_event,
                    text=f"✅ **Notifikasi Blokir (Group)**\n\nPengguna berikut telah keluar dari group ini dan berhasil diblokir:\n\n▪️ **Nama**: {leaving_user.full_name}\n▪️ **Username**: @{leaving_user.username or 'Tidak ada'}\n▪️ **ID**: `{leaving_user.id}`",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Gagal memblokir {leaving_user.id} di group {chat_id_of_event} (pengaturan group): {e}")
                # Kirim notifikasi gagal ke grup itu sendiri
                await context.bot.send_message(
                    chat_id=chat_id_of_event,
                    text=f"❌ **Gagal Memblokir (Group)**\n\nGagal memblokir {leaving_user.full_name} di group ini.\n**Error**: `{e}`\n\nPastikan bot masih menjadi admin dengan izin ban."
                )

def main() -> None:
    """Menjalankan Bot."""
    # PicklePersistence akan otomatis menangani penyimpanan data untuk user_data dan chat_data
    persistence = PicklePersistence(filepath="my_bot_data.pkl")
    application = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    # --- DAFTAR HANDLER BOT ---
    # Handler untuk Command /start (universal untuk chat pribadi dan grup)
    application.add_handler(CommandHandler("start", start))

    # Handler untuk verifikasi bergabung channel wajib (hanya untuk chat pribadi)
    application.add_handler(CallbackQueryHandler(verify_join_callback, pattern='^verify_join$'))

    # ConversationHandler untuk alur pengaturan channel target (hanya untuk chat pribadi)
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_set_channel, pattern='^set_channel$')],
        states={
            GET_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel_id_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_user=True,
        map_to_parent={ ConversationHandler.END: ConversationHandler.END, }
    )
    application.add_handler(conv_handler)

    # Handler untuk toggle fitur ban channel pribadi dan panduan cara pakai
    application.add_handler(CallbackQueryHandler(toggle_channel_ban_callback, pattern='^toggle_channel_ban$'))
    application.add_handler(CallbackQueryHandler(how_to_use_channel_callback, pattern='^how_to_use_channel$'))
    application.add_handler(CallbackQueryHandler(back_to_main_menu, pattern='^back_to_main$'))

    # Handler untuk toggle fitur ban group dan panduan cara pakai di grup
    application.add_handler(CallbackQueryHandler(toggle_group_ban_callback, pattern='^toggle_group_ban$'))
    application.add_handler(CallbackQueryHandler(how_to_use_group_callback, pattern='^how_to_use_group$'))
    application.add_handler(CallbackQueryHandler(back_to_group_menu, pattern='^back_to_group_menu$'))

    # Handler universal untuk update status anggota (mendeteksi user keluar dari channel/group)
    application.add_handler(ChatMemberHandler(handle_member_update, ChatMemberHandler.CHAT_MEMBER))

    # --- Jalankan Bot dalam Mode Polling ---
    # Bot akan selalu berjalan dalam mode polling, cocok untuk lingkungan lokal seperti Termux.
    logger.info("Bot running locally via polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
