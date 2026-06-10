import os
import logging
import anthropic
import requests
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

PREGUNTAS = [
    "Cuéntame en 2-3 frases: ¿qué le dices a un paciente cuando te pregunta por esto por primera vez?",
    "¿Cuál es el miedo más común que tienen los pacientes sobre este tema y cómo lo resuelves?",
    "¿Qué detalle técnico o clínico diferencia tu enfoque como cirujano maxilofacial formado en Londres?",
    "¿Hay algún error frecuente que cometan los pacientes o que veas en otras consultas sobre este tema?",
    "¿Qué le dirías a un paciente ideal para este procedimiento que está dudando en dar el paso?",
]

conversaciones = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in conversaciones:
        del conversaciones[user_id]
    await update.message.reply_text(
        "Hola Gonzalo 👋\n\nSoy tu asistente de contenido para el blog.\n\n"
        "Envíame el TEMA o KEYWORD sobre el que quieres escribir y te haré 5 preguntas. "
        "Responde cada una con una nota de voz y generaré el artículo SEO completo.\n\n"
        "Ejemplo: *cicatrices lifting cervicofacial*",
        parse_mode='Markdown'
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in conversaciones:
        conversaciones[user_id] = {'tema': text, 'respuestas': [], 'pregunta_actual': 0, 'esperando_confirmacion': False}
        await update.message.reply_text(
            f"Perfecto, vamos a crear un artículo sobre: *{text}*\n\n"
            "Ahora te haré 5 preguntas. Puedes responder con nota de voz o texto.\n",
            parse_mode='Markdown'
        )
        await enviar_pregunta(update, user_id)
        return

    estado = conversaciones[user_id]

    if estado.get('esperando_confirmacion'):
        await update.message.reply_text("Por favor usa los botones ✅ / ✏️ para confirmar o corregir tu respuesta.")
        return

    await procesar_respuesta(update, user_id, text)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in conversaciones:
        await update.message.reply_text("Primero dime el tema sobre el que quieres escribir.")
        return

    estado = conversaciones[user_id]
    if estado.get('esperando_confirmacion'):
        await update.message.reply_text("Por favor usa los botones ✅ / ✏️ primero.")
        return

    await update.message.reply_text("Transcribiendo tu nota de voz... 🎙️")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
        tmp_path = tmp.name

    await file.download_to_drive(tmp_path)

    with open(tmp_path, 'rb') as audio_file:
        headers = {'Authorization': f'Bearer {OPENAI_API_KEY}'}
        files_data = {
            'file': ('audio.ogg', audio_file, 'audio/ogg'),
            'model': (None, 'whisper-1'),
            'language': (None, 'es')
        }
        response = requests.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers=headers,
            files=files_data
        )

    os.unlink(tmp_path)

    if response.status_code == 200:
        transcripcion = response.json().get('text', '')
        await update.message.reply_text(f"Entendido: _{transcripcion}_", parse_mode='Markdown')
        await procesar_respuesta(update, user_id, transcripcion)
    else:
        await update.message.reply_text("Error al transcribir. Intenta de nuevo o responde con texto.")

async def procesar_respuesta(update: Update, user_id: int, texto: str):
    estado = conversaciones[user_id]
    estado['respuesta_pendiente'] = texto
    estado['esperando_confirmacion'] = True

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="confirmar"),
            InlineKeyboardButton("✏️ Corregir", callback_data="corregir"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    idx = estado['pregunta_actual']
    await update.message.reply_text(
        f"*Tu respuesta a la pregunta {idx+1}:*\n_{texto}_\n\n¿La confirmamos?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in conversaciones:
        await query.edit_message_text("Sesión expirada. Escribe /start para empezar.")
        return

    estado = conversaciones[user_id]

    if query.data == "confirmar":
        texto = estado.pop('respuesta_pendiente', '')
        estado['esperando_confirmacion'] = False
        estado['respuestas'].append(texto)
        estado['pregunta_actual'] += 1

        await query.edit_message_text(f"✅ *Respuesta guardada.*", parse_mode='Markdown')

        if estado['pregunta_actual'] < len(PREGUNTAS):
            await context.bot.send_message(chat_id=query.message.chat_id,
                text=f"*Pregunta {estado['pregunta_actual']+1}/5:*\n\n{PREGUNTAS[estado['pregunta_actual']]}",
                parse_mode='Markdown')
        else:
            await generar_articulo(query, context, user_id)

    elif query.data == "corregir":
        estado['esperando_confirmacion'] = False
        estado.pop('respuesta_pendiente', '')
        idx = estado['pregunta_actual']
        await query.edit_message_text("✏️ *Escribe o graba tu respuesta corregida:*", parse_mode='Markdown')
        await context.bot.send_message(chat_id=query.message.chat_id,
            text=f"*Pregunta {idx+1}/5:*\n\n{PREGUNTAS[idx]}",
            parse_mode='Markdown')

async def enviar_pregunta(update: Update, user_id: int):
    estado = conversaciones[user_id]
    idx = estado['pregunta_actual']
    await update.message.reply_text(
        f"*Pregunta {idx+1}/5:*\n\n{PREGUNTAS[idx]}",
        parse_mode='Markdown'
    )

async def generar_articulo(query, context, user_id: int):
    estado = conversaciones[user_id]
    tema = estado['tema']
    respuestas = estado['respuestas']

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="✍️ Generando tu artículo optimizado para SEO... (puede tardar 30 segundos)"
    )

    respuestas_texto = '\n'.join([
        f'P{i+1}: {PREGUNTAS[i]}\nR{i+1}: {respuestas[i]}'
        for i in range(len(respuestas))
    ])

    prompt = f"""Eres un experto en SEO médico y copywriting para cirujanos. Escribe un artículo de blog completo y optimizado para SEO sobre el tema: "{tema}"

El autor es el Dr. Gonzalo Botella, cirujano maxilofacial y estético formado en St. George's Hospital de Londres, actualmente en Valencia, España.

USA ESTAS RESPUESTAS DEL DOCTOR para dar autenticidad y E-E-A-T real al artículo:
{respuestas_texto}

ESTRUCTURA DEL ARTÍCULO:
1. Meta title (max 60 caracteres, incluye keyword principal)
2. Meta description (max 155 caracteres)
3. H1 con la keyword principal
4. Introducción (150 palabras) que enganche y responda la intención de búsqueda
5. 3-4 secciones H2 con contenido útil (usa las respuestas del doctor)
6. Sección FAQ con mínimo 3 preguntas y respuestas de 50-80 palabras
7. Conclusión con CTA suave hacia consulta

REQUISITOS SEO:
- Keyword principal en H1, primer párrafo y al menos 2 H2
- Tono profesional pero cercano, como explica un médico a un paciente
- Menciona la formación en Londres y la experiencia clínica real
- Longitud total: 1200-1600 palabras
- Escribe en español de España

Devuelve el artículo completo listo para copiar en WordPress."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    articulo = message.content[0].text

    MAX_LEN = 4000
    if len(articulo) > MAX_LEN:
        partes = [articulo[i:i+MAX_LEN] for i in range(0, len(articulo), MAX_LEN)]
        for i, parte in enumerate(partes):
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"*Parte {i+1}/{len(partes)}:*\n\n{parte}",
                parse_mode='Markdown'
            )
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text=articulo)

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="✅ Artículo generado. Revísalo, añade tu toque personal y publícalo en WordPress.\n\n¿Quieres crear otro? Envíame el siguiente tema."
    )
    del conversaciones[user_id]

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info('Bot iniciado v2')
    app.run_polling()

if __name__ == '__main__':
    main()
