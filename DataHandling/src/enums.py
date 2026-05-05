from pathlib import Path

class ENUMS:
    """
    This class contains all the enums used in the project.
    """

    def __init__(self):
        self.root_folder = "."
        self.config_key_path = str(Path("config") / "config_key.txt")
        self.gemini_model_name = "models/gemini-2.0-flash"  # made this into a single string as "Path" was giving errors
        self.config_key_not_found_error = "config_key.txt not found. please create this file with your gemini api key."
        self.embedding_model_name = "BAAI/bge-small-en-v1.5"
        self.chat_history_json_path = str(
            Path(self.root_folder) / "output" / "chat_history.json"
        )
        self.medical_interview_history_json_path = str(
            Path("output") / "medical_interview_history.json"
        )
        # self.collection_name = "text_collection"
        self.git_collection_name = "single_book_collection"
        # self.db_path = str(
        #     Path(self.root_folder) / "db" / "vector" / "chroma_db_text - Copy"
        # )
        self.git_db_path = str(Path(self.root_folder) / "db" / "vector" / "single_book")
        self.chat_store_key = "conversation1"
        self.chat_mode = "context"
