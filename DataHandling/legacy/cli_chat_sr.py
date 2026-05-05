from src.llm.functionalities import HealthAgent
import speech_recognition as sr
import pyttsx3

# define ChatUI object below and replace reverse_text function
# obj = HealthAgent()


def reverse_text(text):
    # placeholder function, replace with actual work
    # response = obj.main_processor()
    # return response
    return text[::-1]


def speak_text(text):
    # tts
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


def listen_and_process():
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 4.0  # seconds of silence before considering the phrase complete
    mic = sr.Microphone()

    with mic as source:
        print("listening...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

    try:
        text = recognizer.recognize_google(audio)
        print(f"transcribed: {text}")
        reversed_text = reverse_text(text)
        print(f"proce4ssed: {reversed_text}")
        speak_text(reversed_text)
    except sr.UnknownValueError:
        print("could not understand audio")
    except sr.RequestError:
        print("error with SR")


if __name__ == "__main__":
    while True:
        listen_and_process()
