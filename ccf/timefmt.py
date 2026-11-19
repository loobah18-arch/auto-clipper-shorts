def hms(seconds):
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
