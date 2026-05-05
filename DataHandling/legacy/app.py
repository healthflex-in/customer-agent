import os
import warnings

from ui import CustomerChatUI

"""
Disable TensorFlow warnings and set environment variables for performance optimizations.
"""

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

"""
Define the main function to start the customer agent chatbot.
"""


def main():
    customer_agent_ui = CustomerChatUI()
    demo = customer_agent_ui.chat()
    demo.queue()
    demo.launch(debug=True)


if __name__ == "__main__":
    main()
