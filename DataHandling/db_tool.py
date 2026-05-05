import argparse
import json
from datetime import datetime
from tabulate import tabulate
from pprint import pprint

# Import database connector
from db import MedicalInterviewDB

"""
Admin tool for viewing and managing medical interview data in MongoDB.
Provides a command-line interface for basic operations.
"""


def format_timestamp(timestamp):
    """Format a timestamp for display"""
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except:
            return timestamp

    if isinstance(timestamp, datetime):
        return timestamp.strftime("%Y-%m-%d %H:%M:%S")

    return str(timestamp)


def list_users(db, args):
    """List all users in the database"""
    users = list(db.users.find({}).sort("created_at", -1))

    if not users:
        print("No users found.")
        return

    # Format user data for display
    user_data = []
    for user in users:
        user_data.append(
            {
                "User ID": user.get("user_id"),
                "Created At": format_timestamp(user.get("created_at")),
                "Metadata": json.dumps(user.get("metadata", {})),
            }
        )

    # Display table
    print(tabulate(user_data, headers="keys", tablefmt="grid"))
    print(f"Total users: {len(users)}")


def list_interviews(db, args):
    """List all interviews in the database"""
    # If user_id provided, filter by user
    filter_query = {}
    if args.user_id:
        filter_query["user_id"] = args.user_id

    interviews = list(db.interviews.find(filter_query).sort("created_at", -1))

    if not interviews:
        if args.user_id:
            print(f"No interviews found for user {args.user_id}.")
        else:
            print("No interviews found.")
        return

    # Format interview data for display
    interview_data = []
    for interview in interviews:
        interview_data.append(
            {
                "Interview ID": interview.get("interview_id"),
                "User ID": interview.get("user_id"),
                "Created At": format_timestamp(interview.get("created_at")),
                "Updated At": format_timestamp(interview.get("updated_at")),
                "Current Section": interview.get("current_section"),
                "Progress": f"{interview.get('progress', 0):.1f}%",
                "Completed": "Yes" if interview.get("completed", False) else "No",
            }
        )

    # Display table
    print(tabulate(interview_data, headers="keys", tablefmt="grid"))
    print(f"Total interviews: {len(interviews)}")


def view_interview(db, args):
    """View details of a specific interview"""
    if not args.interview_id:
        print("Error: Interview ID is required.")
        return

    # Get interview data
    interview = db.get_interview(args.interview_id)

    if not interview:
        print(f"No interview found with ID: {args.interview_id}")
        return

    # Display interview details
    print("=" * 80)
    print(f"INTERVIEW DETAILS - {args.interview_id}")
    print("=" * 80)

    print(f"User ID: {interview.get('user_id')}")
    print(f"Session ID: {interview.get('session_id')}")
    print(f"Created At: {format_timestamp(interview.get('created_at'))}")
    print(f"Updated At: {format_timestamp(interview.get('updated_at'))}")
    if interview.get("completed", False):
        print(f"Completed At: {format_timestamp(interview.get('completed_at'))}")
    print(f"Current Section: {interview.get('current_section')}")
    print(f"Progress: {interview.get('progress', 0):.1f}%")
    print(f"Completed: {'Yes' if interview.get('completed', False) else 'No'}")

    # Display form data
    print("\nFORM DATA:")
    pprint(interview.get("form_data", {}))

    # If verbose, also show interactions
    if args.verbose:
        interactions = db.get_interview_interactions(args.interview_id)

        if interactions:
            print("\nINTERACTIONS:")
            for i, interaction in enumerate(interactions):
                print(
                    f"\n[{i+1}] {interaction.get('type')} - {format_timestamp(interaction.get('timestamp'))}"
                )
                print(f"Content: {interaction.get('content')}")
                if i < len(interactions) - 1:
                    print("-" * 40)
        else:
            print("\nNo interactions found for this interview.")


def main():
    parser = argparse.ArgumentParser(description="Medical Interview Admin Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Users list command
    users_parser = subparsers.add_parser("users", help="List all users")

    # Interviews list command
    interviews_parser = subparsers.add_parser("interviews", help="List all interviews")
    interviews_parser.add_argument("--user-id", help="Filter by user ID")

    # View interview command
    view_parser = subparsers.add_parser("view", help="View interview details")
    view_parser.add_argument("interview_id", help="Interview ID to view")
    view_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show more details"
    )

    # Parse arguments
    args = parser.parse_args()

    # Connect to database
    try:
        db = MedicalInterviewDB()

        # Execute command
        if args.command == "users":
            list_users(db, args)
        elif args.command == "interviews":
            list_interviews(db, args)
        elif args.command == "view":
            view_interview(db, args)
        else:
            parser.print_help()

        # Close database connection
        db.close()

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
