import argparse
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, List, Optional, Tuple

from googleapiclient.discovery import build

import constants as c
import zoom as z
from alfred import *


class Command(Enum):
    list = "list"
    join = "join"


class OutputFormat(Enum):
    stdout = "stdout"
    alfred = "alfred"


class NextMeetingOptions(Enum):
    """The options for what we found on your calendar"""
    # We found 1 meeting you should be in 
    FoundNextMeeting = "FoundNextMeeting"
    # We found multiple meetings you should be in
    MultipleOptions = "MultipleOptions"
    # We didn't find any meeting you should be in
    NoOptions = "NoOptions"


@dataclass
class Args():
    # meeting: str = None
    # autojoin: bool = False
    command: Command
    format: OutputFormat = OutputFormat.stdout
    now: datetime = datetime.now(tz=timezone.utc)


def valid_datetime_type(arg_datetime_str):
    """custom argparse type for user datetime values given from the command line"""
    try:
        return datetime.fromisoformat(arg_datetime_str)
    except ValueError:
        msg = f"Given Datetime ({arg_datetime_str}) not valid! Expected ISO format, 'YYYY-MM-DDTHH:mm:ss.mmmmmmZ'!"
        raise argparse.ArgumentTypeError(msg)  


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse your calendar looking for the next zoom meeting")
    parser.add_argument("-c", "--command", dest="command", choices=tuple(e.value for e in Command), required=True, help="The command to run")
    # parser.add_argument("--autojoin", default=False, action=argparse.BooleanOptionalAction, help="Autojoin the next meeting?")
    parser.add_argument("-f", "--format", dest="format", default=OutputFormat.stdout.value, choices=tuple(e.value for e in OutputFormat), help="Format for the output")
    parser.add_argument("-n", "--now", dest="now", default=datetime.now(tz=timezone.utc), type=valid_datetime_type, help="Optional override time to use for 'now'")
    args = parser.parse_args()
    return Args(
        command=Command[args.command],
        format = OutputFormat[args.format],
        now=args.now,
    )


def _debug(message: str, format: OutputFormat = OutputFormat.stdout) -> None:
    if format == OutputFormat.stdout:
        print(message)
    else:
        print(message, file=sys.stderr)


def fetch_events(args: Args) -> List[dict]:
    creds = z._fetch_creds()
    service = build('calendar', 'v3', credentials=creds)

    # Call the Calendar API
    now: datetime = args.now
    time_min: str = now.isoformat()
    time_max: str = (now + timedelta(hours=c.HOURS_AHEAD)).isoformat()
    _debug(f"Getting the upcoming {c.NUM_NEXT} events from {time_min} to {time_max}", args.format)
    events_result = service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        maxResults=c.NUM_NEXT,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])
    return events


def has_zoom_link(event: dict, args: Args) -> bool:
    return get_zoom_link(event, args) is not None


def get_zoom_link(event: dict, args: Args) -> str:
    """Return the zoom link from the event data if it exists"""
    # First let's look in the location
    loc = event.get("location", "")
    if "zoom.us" in loc:
        # If the meeting is setup as zoom as a location, then it may have multiple.
        # Split as csv and find the first with zoom.us in it.
        if "," in loc:
            loc = next(l for l in loc.split(",") if "zoom.us" in l)
        return loc
    
    eps = event.get("conferenceData", {}).get("entryPoints", [])
    # Sometimes an event returns an empty array here which results in a StopIteration
    # error when we try to use the following for loop
    if eps:
        found = next(ep for ep in eps if "zoom.us" in ep.get("uri", ""))
        if found:
            return found["uri"]
        else:
            _debug("Conference data found, but no zoom links in it for " + event["summary"], args.format)
    else:
        _debug("No conference data found for " + event["summary"], args.format)

    return None


@dataclass
class MyEvent():
    id: str
    start: datetime
    summary: str
    is_not_day_event: bool = True
    # Is the current meeting in progress
    in_progress: bool = False
    # Does this event start within a "joinable" time frame?
    is_next_joinable: bool = False
    zoom_link: Optional[str] = None
    icon: Optional[str] = None

    def to_item(self) -> Item:
        return Item(
            uid=self.id,
            title=self.summary,
            subtitle=f"Starting at {self.start}",
            arg=self.zoom_link,
            variables=dict(
                title=self.summary,
                start=self.start,
            ),
            icon=(ItemIcon(path=self.icon) if self.icon else None)
        )


def parse_event_datetime(d: Dict[str, str]) -> Optional[datetime]:
    datetime_or_date = d.get("dateTime", d.get("date"))
    # TODO: deal with timezones...
    # timezone = d.get("timeZone")
    try:
        return datetime.fromisoformat(datetime_or_date)
    except:
        # TODO: better error handling
        return None


def parse_events(events: List[dict], args: Args) -> List[MyEvent]:
    """Converts a Google calendar event (dict) into a MyEvent"""
    def augment(event: dict):
        id = event["id"]

        start = parse_event_datetime(event["start"])
        end = parse_event_datetime(event["end"])
        is_not_day = z.is_not_day_only(event)
        in_progress = is_not_day and start <= args.now and end >= args.now
        is_next_joinable = False
        if is_not_day and not in_progress and start > args.now:
            delta = start - args.now
            is_next_joinable = delta < timedelta(minutes=c.JOINABLE_IF_NEXT_STARTS_WITHIN)

        summary = event["summary"]

        link = get_zoom_link(event, args)
        zoom_link = None
        if link:
            zoom_link = z.convert_to_zoom_protocol(link)

        icon = None
        if "1:1" in summary:
            icon = "one.png"
        elif "Standup" in summary:
            icon = path="standup.png"
        else:
            icon = "icon.png"

        return MyEvent(
            id=id,
            start=start,
            summary=summary,
            is_not_day_event=is_not_day,
            zoom_link=zoom_link,
            in_progress=in_progress,
            is_next_joinable=is_next_joinable,
            icon=icon,
        )


    return list(map(augment, events))


def find_meeting_to_join(events: List[MyEvent], args: Args) -> Tuple[NextMeetingOptions, Optional[MyEvent]]:
    """
    Given a list of meetings to join, find the one we should join

    ...but this will only do so if the meeting to join is "obvious". Meaning
    there aren't multiple meetings happening at the same time (with some
    caveats) and that the next meeting is starting eminently.
    """
    _debug(f"Looking for next meeting. Have {len(events)} candidates", args.format)
    if events:
        in_progress = [e for e in events if e.in_progress]
        next = [e for e in events if e.is_next_joinable]
        if len(next) == 1:
            return NextMeetingOptions.FoundNextMeeting, next[0]
        if len(in_progress) == 1:
            return NextMeetingOptions.FoundNextMeeting, in_progress[0]
        
        # Too much ambiguity, display the list downstream for the user to pick.
        return NextMeetingOptions.MultipleOptions, None

    return NextMeetingOptions.NoOptions, None


def _debug_event_list(events: List[MyEvent], format: OutputFormat) -> None:
    """Debug each event in a well formatted manner"""
    for event in events:
        event_string = f"""\
        Event: {event.id}
          Summary: {event.summary}
          Start  : {event.start}
          Markers: {event.is_not_day_event} | {event.in_progress} | {event.is_next_joinable}
          Link   : {event.zoom_link}"""
        _debug(textwrap.dedent(event_string), format)


def command_list(args: Args) -> None:
    """Implement the list command"""
    events = fetch_events(args)
    events: List[MyEvent] = parse_events(events, args)

    _debug_event_list(events, args.format)

    filtered_events = [e for e in events if e.is_not_day_event and e.zoom_link]

    if args.format == OutputFormat.alfred:
        items = [e.to_item() for e in filtered_events]
        output = ScriptFilterOutput(items=items)
        next_meeting_value, to_join = find_meeting_to_join(filtered_events, args)
        vars = dict(
            # bool values show up as 0/1 in Alfred.
            need_to_prompt=True,
            next_meeting=next_meeting_value.value,
        )
        if to_join:
            vars.update(
                zoom_link=to_join.zoom_link,
                title=to_join.summary,
                start=to_join.start,
            )
        utility_output = JsonUtilityFormat(
            alfredworkflow=AlfredWorkflow(
                arg=output.to_json(),
                config=dict(),
                variables=vars,
            )
        )
        print(utility_output.to_json())
    else:
        print("TODO: Figure out the non-alfred output format...")
    

def main() -> None:
    args: Args = parse_args()

    if args.command == Command.list:
        command_list(args)
    elif args.command == Command.join:
        pass
    else:
        pass


if __name__ == '__main__':
    main()
