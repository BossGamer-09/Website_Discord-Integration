def user_can_view_killtracker(user) -> bool:
    """Show the KillTracker app on the main index. Leaderboard itself is public."""
    if not user or not user.is_authenticated:
        return False
    return user.has_perm("killtracker.can_generate_key") or user.has_perm("killtracker.can_view_leaderboard")
