from src.llm.functionalities import HealthAgent
import speech_recognition as sr
import pyttsx3
import os

# Initialize the health agent
health_agent = HealthAgent()
recognizer = sr.Recognizer()
recognizer.pause_threshold = 1.5  # Longer pause threshold for natural conversation
engine = pyttsx3.init()
interview_started = False
history = []


def speak_text(text):
    """Convert text to speech and speak it."""
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS Error: {e}")


def listen_for_speech(timeout=10, phrase_time_limit=None):
    """
    Listen for user speech and convert to text.

    Args:
        timeout: How long to wait before giving up when no speech is detected
        phrase_time_limit: Maximum allowed duration for a phrase

    Returns:
        Transcribed text or None if no speech detected
    """
    mic = sr.Microphone()

    with mic as source:
        print("Listening... (speak now)")
        recognizer.adjust_for_ambient_noise(source, duration=1)
        try:
            audio = recognizer.listen(
                source, timeout=timeout, phrase_time_limit=phrase_time_limit
            )
            print("Processing your response...")
        except sr.WaitTimeoutError:
            print("No speech detected within timeout period")
            return None

    try:
        text = recognizer.recognize_google(audio)
        print(f"Transcribed: {text}")
        return text
    except sr.UnknownValueError:
        print("Could not understand audio")
        return None
    except sr.RequestError as e:
        print(f"Error with speech recognition service: {e}")
        return None


def check_if_interview_accepted(text):
    """Check if the user has agreed to start the interview."""
    if not text:
        return False

    text = text.lower()
    positive_responses = [
        "yes",
        "yeah",
        "sure",
        "okay",
        "ok",
        "fine",
        "alright",
        "ready",
        "let's start",
        "let's begin",
        "start",
        "begin",
        "please",
        "go ahead",
    ]

    for response in positive_responses:
        if response in text:
            return True

    return False


def process_user_input(text):
    """
    Process user input and get the next response from the HealthAgent.

    Args:
        text: User's transcribed speech

    Returns:
        The agent's response text
    """
    global interview_started

    if not text:
        return "I didn't catch that. Could you please repeat?"

    # Check if the interview is accepted
    if not interview_started:
        if check_if_interview_accepted(text):
            interview_started = True
            return health_agent.main_processor("")  # Start the interview
        else:
            return health_agent.interview_declined_message

    # Check relevancy of user input
    relevancy_action, nudge_message = health_agent.check_relevancy_and_get_action(text)

    # If input requires a nudge, provide the nudge message
    if relevancy_action == "Nudge":
        print("\n[Topic guidance needed - off-topic detected]")
        # Add to history but note that it was off-topic
        history.append({"role": "user", "message": text, "off_topic": True})
        history.append({"role": "agent", "message": nudge_message, "is_nudge": True})
        return nudge_message

    # For debugging - show the relevancy action
    if relevancy_action in ["Silent", "Nod"]:
        print(f"\n[Topic is relevant - {relevancy_action}]")

    # Process normally through HealthAgent
    response = health_agent.main_processor(text)

    # Add to history
    history.append({"role": "user", "message": text})
    history.append({"role": "agent", "message": response})

    return response


def display_progress():
    """Display interview progress to the user."""
    if not health_agent.form_sections:
        return

    try:
        current_index = health_agent.form_sections.index(health_agent.current_section)
        total_sections = len(health_agent.form_sections)
        progress = (current_index / total_sections) * 100

        # Additional progress within current section
        if health_agent.missing_fields:
            total_fields = len(health_agent.form[health_agent.current_section])
            if total_fields > 0:
                section_progress = (
                    total_fields - len(health_agent.missing_fields)
                ) / total_fields
                section_contribution = (section_progress / total_sections) * 100
                progress += section_contribution

        progress = min(progress, 99)  # Cap at 99% until completely finished

        progress_bar_length = 30
        filled_length = int(progress_bar_length * progress / 100)
        bar = "█" * filled_length + "░" * (progress_bar_length - filled_length)
        print(f"\nInterview Progress: |{bar}| {progress:.1f}%\n")
    except Exception as e:
        print(f"Error displaying progress: {e}")


def main_interview_loop():
    """Main loop for conducting the interview."""
    global interview_started

    # Start with initial question
    if not interview_started:
        response = health_agent.ready_to_start_message
    else:
        response = health_agent.main_processor("")  # Get first question

    print("\nMEDICAL ASSISTANT:", response)
    speak_text(response)

    try:
        while True:
            # Display current progress
            display_progress()

            # Listen for user speech
            user_text = listen_for_speech()

            if user_text:
                # Store user input for relevancy checking
                health_agent.info = user_text

                # Process the user's response
                agent_response = process_user_input(user_text)

                # Check if interview is complete
                if (
                    "Thank you for completing" in agent_response
                    or health_agent.invalid_index()
                ):
                    print("\nMEDICAL ASSISTANT:", agent_response)
                    speak_text(agent_response)
                    print("\nInterview completed and saved successfully.")
                    health_agent.save_progress()
                    break

                # Output the response
                print("\nMEDICAL ASSISTANT:", agent_response)
                speak_text(agent_response)
            else:
                print("\nI didn't hear anything. Please try again.")

    except KeyboardInterrupt:
        print("\n\nInterview interrupted by user. Saving progress...")
        health_agent.save_progress()
        print("Progress saved. Goodbye!")


def start_cli_interview():
    """Start the medical interview CLI."""
    # Clear the console for better UX
    os.system("cls" if os.name == "nt" else "clear")

    print("=" * 60)
    print("MEDICAL INTERVIEW ASSISTANT - COMMAND LINE INTERFACE")
    print("=" * 60)
    print("\nInitializing system...")

    print("\nWelcome to the Medical Interview System")
    print("This system will ask you questions about your medical history.")
    print("Please speak clearly and answer the questions as best you can.")
    print("\nWould you like to begin the interview? (Say 'yes' to start)")

    # Start the interview
    main_interview_loop()


if __name__ == "__main__":
    start_cli_interview()
