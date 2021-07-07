# How many hours ahead worth of events should we fetch.
HOURS_AHEAD = 9
# How many calendar events to fetch at a time.
NUM_NEXT = 5
# Mark this event as joinable if it starts within this many minutes...used to
# skip the current in-progress meeting for cases where a meeting ends at 10am
# and the next one starts at 10am and you want to join the next one at 9:59am.
JOINABLE_IF_NEXT_STARTS_WITHIN = 3
