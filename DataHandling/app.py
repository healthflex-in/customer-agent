import os
import warnings
import argparse
from ui import CustomerChatUI
from cli import start_cli_interview

"""
Disable TensorFlow warnings and set environment variables for performance optimizations.
"""

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

"""
Define the main function to start the customer agent in either UI or CLI mode.
"""


def main():
    # parser = argparse.ArgumentParser(description="Medical Interview Assistant")
    # parser.add_argument(
    #     "--cli",
    #     action="store_true",
    #     help="Run in command-line interface mode instead of GUI",
    # )
    # args = parser.parse_args()

    # if args.cli:
    #     # Run CLI version
    #     print("Starting Medical Interview Assistant in CLI mode...")
    #     start_cli_interview()
    # else:
    #     # Run GUI version
    #     print("Starting Medical Interview Assistant in GUI mode...")
    #     customer_agent_ui = CustomerChatUI()
    #     demo = customer_agent_ui.chat()
    #     demo.queue()
    #     demo.launch(debug=True)

    print("Starting Medical Interview Assistant in CLI mode...")
    start_cli_interview()


if __name__ == "__main__":
    main()
