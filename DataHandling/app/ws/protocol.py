"""WebSocket inbound/outbound message type constants."""

class Inbound:
    START_INTERVIEW = "start_interview"
    START_NEW_FORM  = "start_new_form"
    LOAD_FORM       = "load_form"
    TEXT_INPUT      = "text_input"
    AUDIO_START     = "audio_start"
    AUDIO_CHUNK     = "audio_chunk"
    AUDIO_END       = "audio_end"
    END_SESSION     = "end_session"

class Outbound:
    TEXT_MESSAGE    = "text_message"
    TOKEN           = "token"
    TRANSCRIPTION   = "transcription"
    AUDIO_START     = "audio_start"
    AUDIO_CHUNK     = "audio_chunk"
    FORM_LOADED     = "form_loaded"
    THOUGHT_UPDATE  = "thought_update"
    CHAT_HISTORY    = "chat_history"
    ERROR           = "error"
    SYSTEM          = "system"
