import base64
import json
import ijson
import re
import datetime

import pandas as pd

from collections import Counter
from pathlib import Path

"""
Decoding scripts are based on the php and cpp scripts found at https://tagpro.eu/?science.
Copyright of the original author Jeroen va der Gun is copied below:

// Copyright (c) 2020, Jeroen van der Gun
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without modification, are
// permitted provided that the following conditions are met:
//
// 1. Redistributions of source code must retain the above copyright notice, this list of
//    conditions and the following disclaimer.
//
// 2. Redistributions in binary form must reproduce the above copyright notice, this list of
//    conditions and the following disclaimer in the documentation and/or other materials
//    provided with the distribution.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY
// EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
// MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
// THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
// OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
// HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
// TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
// SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

class LogReader:
    def __init__(self, data):
        """
        self.pos tracks the current bit we are reading
        """
        self.data = data
        self.pos = 0

    def end(self):
        """
        Returns whether or not we are at the end of the events
        Returns 1 if current position is greater than or equal to the length of the data; 0 otherwise
        """
        return self.pos >> 3 >= len(self.data)

    def read_bool(self):
        """
        Returns a bit (1 or 0) indicating whether something happened at a time step
        Every time this function is called, we increment position by 1

        PHP 
        $result = $this->end() ? 0 : ord($this->data[$this->pos >> 3]) >> 7 - ($this->pos & 7) & 1;

        CPP
        result = eof() ? 0 : data[pos >> 3] >> (7 - (pos & 7)) & 1;
        """
        result = 0 if self.end() else (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return result

    def read_fixed(self, bits):
        """
        Returns an integer less than 2^bits stored in a fixed number of bits
        """
        result = 0
        while bits:
            result = (result << 1) | self.read_bool()
            bits -= 1

        return result

    def read_tally(self):
        """
        Reads until read_bool returns non-stop value
        """
        result = 0
        while self.read_bool():
            result += 1
        return result

    def read_footer(self):
        """
        Reads the footer of the bitwise
        TODO: Check order of functions
        """
        bits = self.read_fixed(2) << 3
        free = (8 - (self.pos & 7)) & 7
        bits |= free
        minimum = 0
        while free < bits:
            minimum += 1 << free
            free += 8
        return self.read_fixed(bits = bits) + minimum


class PlayerLogReader(LogReader):
    def __init__(self, data = None, match_id = None, name = None, name_reserved = None, degree = None, date = None, map_id = None, team = None, duration = None):
        super().__init__(data)
        self.name = name
        self.name_reserved = name_reserved
        self.degree = degree
        self.match_id = match_id
        self.date = date
        self.datetime = datetime.datetime.fromtimestamp(date)
        self.map_id = map_id
        self.team = team # In new records, value indicates team at start of match
        self.duration = duration
        
        # Constants
        self.no_team = 0
        self.red_team = 1
        self.blue_team = 2

        self.no_flag = 0
        self.opponent_flag = 1
        self.opponent_potato_flag = 2
        self.neutral_flag = 3
        self.neutral_potato_flag = 4
        self.temporary_flag = 5

        self.pup_none = 0
        self.pup_jj = 1
        self.pup_rb = 2
        self.pup_tp = 4
        self.pup_topspeed = 8

        # Values of current data
        self.time = 0
        self.flag = self.no_flag
        self.powers = self.pup_none
        self.prevent = False
        self.button = False
        self.block = False

        self.events = []

    def get_player_info(self):
        """
        Helper function for organizing player information
        """
        return {'name' : self.name, 'name_reserved' : self.name_reserved, 'degree' : self.degree, 'match_id' : self.match_id, 'date' : self.date, 'datetime' : self.datetime, 'map_id' : self.map_id, 'team' : self.team, 'duration' : self.duration}

    def decode_events(self):
        """
        Reads through event bytecode, logging events after each chunk. Continues until end of bytecode is decoded.
        Sorts events based on their timestamp.
        """
        # At start of match, if self.team has a non-zero value, then player joined the match prior to start
        if self.team != 0:
            self._log_event(event = 'start', time = 0, team = self.team)

        # Reading of data starts here and continues until end is reached
        while not self.end():
            # Read team information
            if self.read_bool():
                if self.team:
                    if self.read_bool():
                        self.new_team = self.no_team # Quit
                    else:
                        self.new_team = 3 - self.team # Switch
                else:
                    self.new_team = 1 + self.read_bool() # Join
            else:
                self.new_team = self.team # Stay

            self.dropPop = self.read_bool()
            self.returns = self.read_tally()
            self.tags = self.read_tally()
            self.grab = (not self.flag) and self.read_bool()
            self.captures = self.read_tally()
            self.keep = (not self.dropPop) and self.new_team and (self.new_team == self.team or (not self.team)) and ((not self.captures) or ((not self.flag) and (not self.grab)) or self.read_bool())
            
            # If we have grabbed, check which flag
            if self.grab:
                if self.keep:
                    self.newFlag = 1 + self.read_fixed(2)
                else:
                    self.newFlag = self.temporary_flag
            else:
                self.newFlag = self.flag

            # Check powerups
            self.powerups = self.read_tally()
            self.powers_down = self.pup_none
            self.powers_up = self.pup_none
            i = 1
            while i < 16:
                if (self.powers & i):
                    if self.read_bool():
                        self.powers_down |= i
                else:
                    if (self.powerups and self.read_bool()):
                        self.powers_up |= i
                        self.powerups -= 1
                i <<= 1

            # Check whether we are preventing, buttoning, or blocking
            self.togglePrevent = self.read_bool()
            self.toggleButton = self.read_bool()
            self.toggleBlock = self.read_bool()
            
            # Check current time
            self.time += (1 + self.read_footer())

            # Log all events in this loop
            self._log_events()

        # Log end
        self._log_event(event = 'end', time = self.duration, flag = self.flag, powers = self.powers, team = self.team)

        # Sort all events by time
        self.events = sorted(self.events, key = lambda x: x['time'])

        return self.events

    def _log_events(self):
        """
        Logs all events from a section bytecode and flips flags on or off for certain events (e.g. self.flag set to self.no_flag after capture)
        """
        # Now start recording events
        # Tracking join
        if ((not self.team) and self.new_team):
            self.team = self.new_team
            self._log_event(event = 'join', time = self.time, new_team = self.team)

        # Log all return events
        for i in range(self.returns):
            self._log_event(event = 'return', time = self.time, flag = self.flag, powers = self.powers, team = self.team)

        # Log all tag events
        for i in range(self.tags):
            self._log_event(event = 'tag', time = self.time, flag = self.flag, powers = self.powers, team = self.team)

        if self.grab:
            self.flag = self.newFlag # Not sure why this is here
            self._log_event(event = 'grab', time = self.time, flag = self.flag, powers = self.powers, team = self.team)

        # Log all captures
        if self.captures > 0:
            self.captures -= 1
            if self.keep or (not self.flag):
                self._log_event(event = 'flagless_capture', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
            else:
                self._log_event(event = 'capture', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.flag = self.no_flag
                self.keep = True

        # Log all powerups and powerdowns
        i = 1
        while i < 16:
            if self.powers_down & i:
                self.powers ^= i
                self._log_event(event = 'power_down', time = self.time, flag = self.flag, pup = i, new_powers = self.powers, team = self.team)
            else:
                if self.powers_up & i:
                    self.powers |= i
                    self._log_event(event = 'power_up', time = self.time, flag = self.flag, pup = i, new_powers = self.powers, team = self.team)
            i <<= 1
        for i in range(self.powerups):
            self._log_event(event = 'duplicate_powerup', time = self.time, flag = self.flag, powers = self.powers, team = self.team)

        # Log prevent
        if self.togglePrevent:
            if self.prevent:
                self._log_event(event = 'prevent_stop', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.prevent = False
            else:
                self._log_event(event = 'prevent_start', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.prevent = True

        # Log button
        if self.toggleButton:
            if self.button:
                self._log_event(event = 'button_stop', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.button = False
            else:
                self._log_event(event = 'button_start', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.button = True

        # Log block
        if self.toggleBlock:
            if self.block:
                self._log_event(event = 'block_stop', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.block = False
            else:
                self._log_event(event = 'block_start', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.block = True

        # Logs drops and pops
        if self.dropPop:
            if self.flag:
                self._log_event(event = 'drop', time = self.time, flag = self.flag, powers = self.powers, team = self.team)
                self.flag = self.no_flag
            else:
                self._log_event(event = 'pop', time = self.time, powers = self.powers, team = self.team)

        # Log team switch and quit
        if self.new_team != self.team:
            if not self.new_team:
                self._log_event(event = 'quit', time = self.time, flag = self.flag, powers = self.powers, old_team = self.team)
                self.powers = self.pup_none
            else:
                self._log_event(event = 'switch', time = self.time, flag = self.flag, powers = self.powers, old_team = self.team, new_team = self.team)
                self.flag = self.no_flag
                self.team = self.new_team

    def _log_event(self, event = None, frames_to_seconds = False, *args, **event_info):
        """
        Updates event tracker with player information, type of event, and event information
        """
        event_info.update({'event' : event})
        if frames_to_seconds:
            event_info['time'] = round(event_info['time'] / 60, 3) # Times are by default in frames; 60 frames a second
        event_info.update(self.get_player_info())
        self.events.append(event_info)

    # Counters to generate player scoreboard by tallying events and durations
    def _count_events(self, target_events):
        """
        Returns number of events matching target_events
        """
        return len([event for event in self.events if event['event'] in target_events])

    def _count_events_during(self, target_events, on_flag, off_flag):
        """
        Returns number of events matching target_events that occur after on_flag occurs but before off_flag
        """
        event_count = 0
        flag = False

        for event in self.events:
            if event['event'] in on_flag:
                flag = True
            if flag and event['event'] in target_events:
                event_count += 1
            if event['event'] in off_flag:
                flag = False

        return event_count

    def _count_pups(self, target_event, target_pup):
        """
        Counts number of target powerups
        """
        return len([event for event in self.events if (event['event'] == 'power_up') and (event['pup'] == target_pup)])

    def _measure_duration(self, on_duration_events, off_duration_events):
        """
        Returns duration between on and off events.

        For example, held times are determined by a time from a grab to either a drop, a pop, or a capture
        """
        times = []
        flag = False

        for event in self.events:
            if event['event'] in on_duration_events:
                flag = True
                start_time = event['time']
            if flag and event['event'] in off_duration_events:
                flag = False
                times.append(event['time'] - start_time)

        return times

    def _measure_duration_condition(self, on_duration_events, off_duration_events, condition_field, on_flag, off_flag):
        """
        Measures conditional duration that occur between on and off flag events
        """
        times = []
        condition_flag = False
        duration_flag = False

        for event in self.events:
            if event['event'] in on_duration_events:
                if event[condition_field] in on_flag:
                    duration_flag = True
                    start_time = event['time']
            if duration_flag and event['event'] in off_duration_events:
                if event[condition_field] in off_flag:
                    duration_flag = False
                    times.append(event['time'] - start_time)

        return times


    def scoreboard(self):
        """
        Returns scoreboard for a player
        """
        player_info = self.get_player_info()

        num_grabs = self._count_events(target_events = ['grab'])
        hold_times = self._measure_duration(on_duration_events = ['grab'], off_duration_events = ['drop', 'pop', 'capture'])
        captures = self._count_events(target_events = ['capture'])

        returns = self._count_events(target_events = ['return'])
        tags = self._count_events(target_events = ['return', 'tag'])
        prevent_times = self._measure_duration(on_duration_events = ['prevent_start'], off_duration_events = ['prevent_stop'])
        button_times = self._measure_duration(on_duration_events = ['button_start'], off_duration_events = ['button_stop'])
        block_times = self._measure_duration(on_duration_events = ['button_start'], off_duration_events = ['button_stop'])

        kisses = self._count_events_during(target_events = ['return'], on_flag = ['grab'], off_flag = ['drop', 'pop', 'capture'])
        drops = self._count_events(target_events = ['drop'])
        pops = self._count_events(target_events = ['pop'])

        have_time = None # Included on tagpro.eu match scoreboards but not implemented here
        chase_time = None # Included on tagpro.eu match scoreboards but not implemented here

        pup_jj_count = self._count_pups(target_event = ['power_up'], target_pup = self.pup_jj)
        pup_rb_count = self._count_pups(target_event = ['power_up'], target_pup = self.pup_rb)
        pup_tp_count = self._count_pups(target_event = ['power_up'], target_pup = self.pup_tp)
        pup_all_count = pup_jj_count + pup_rb_count + pup_tp_count
        pup_jj_times = self._measure_duration_condition(on_duration_events = ['power_up'], off_duration_events = ['power_down'], condition_field = 'pup', on_flag = [self.pup_jj], off_flag = [self.pup_jj])
        pup_rb_times = self._measure_duration_condition(on_duration_events = ['power_up'], off_duration_events = ['power_down'], condition_field = 'pup', on_flag = [self.pup_rb], off_flag = [self.pup_rb])
        pup_tp_times = self._measure_duration_condition(on_duration_events = ['power_up'], off_duration_events = ['power_down'], condition_field = 'pup', on_flag = [self.pup_tp], off_flag = [self.pup_tp])
        
        play_time = sum(self._measure_duration(on_duration_events = ['start', 'join'], off_duration_events = ['quit', 'end']))

        player_info.update(
            {'grabs' : num_grabs, 'hold_total' : sum(hold_times), 'holds' : hold_times, 'captures' : captures,
            'tags' : tags, 'returns' : returns, 'kisses' : kisses, 'drops' : drops, 'pops' : pops,
            'prevent_total' : sum(prevent_times), 'prevents' : prevent_times,
            'button_total' : sum(button_times), 'buttons' : button_times,
            'block_total' : sum(block_times), 'blocks' : block_times,
            'pups' : pup_all_count, 'pup_jj' : pup_jj_count, 'pup_rb' : pup_rb_count, 'pup_tp' : pup_tp_count,
            'pup_jj_time' : sum(pup_jj_times), 'pup_rb_time' : sum(pup_rb_times), 'pup_tp_times' : sum(pup_tp_times),
            'playtime' : play_time}
        )

        return player_info

# Convenience function to batch read events
def generate_match_info(match_json, match_id,
                        save_flag = False):
    """
    Returns a list of events for a match and a list of scoreboards for the match
    """
    match_events = []
    match_scoreboards = []

    for player in match_json['players']:
        player_reader = PlayerLogReader(data = base64.b64decode(player['events']),
                                        match_id = match_id,
                                        name = player['name'],
                                        name_reserved = player['auth'],
                                        degree = player['degree'],
                                        date = match_json['date'],
                                        map_id = match_json['mapId'],
                                        team = player['team'],
                                        duration = match_json['duration'])
        match_events += player_reader.decode_events() # List of events from decode_events()
        match_scoreboards.append(player_reader.scoreboard()) # Single dictionary scoreboard from scoreboard()

    # Sort player events
    match_events = sorted(match_events, key = lambda x: x['time'])

    # Get other info
    match_events, team_caps = add_current_team_captures(match_events = match_events)
    match_scoreboards = add_winner_to_scoreboard(match_scoreboards = match_scoreboards, team_caps = team_caps)

    if save_flag:
        save_match_info(match_id = match_id, info_to_save = match_events, path_to_savefolder = 'events/match')
        save_match_info(match_id = match_id, info_to_save = match_scoreboards, path_to_savefolder = 'scoreboards/match')

    return match_events, match_scoreboards


def read_matches(matches_json_file,
                 match_range = None,
                 save_match_flag = False,
                 save_bulk_events = True,
                 save_bulk_scoreboards = True,
                 report_flag = False,
                 report_every = 1):
    """
    Reads in matches by reading in the entire json file to memory at once, then iterating over matches. By default,
    all match information will be retained in memory as they are read, where they will be saved at the end.

    match_range: only reads matches that are within range
    save_bulk_events: whether or not events will be retained in memory and eventually saved
    save_bulk_scoreboards: whether or not scoreboards will be retained in memory as they are read and eventually saved
    report_flag: whether or not report will be printed for match after reading
    report_every: how often (i.e. number of matches) reading will be reported
    """
    all_match_events = []
    all_match_scoreboards = []
    match_id_min = None
    match_id_max = None

    with open(matches_json_file) as f:
        match_info = json.load(f)

    for match_id in match_info:
        # Check if match_id in match_range
        if match_range and (int(match_id) not in match_range):
                continue
        if report_flag and (int(match_id) % report_every == 0): print(f'Reading in match {match_id}')

        # Get match information
        match_events, match_scoreboards = generate_match_info(match_json = match_info[match_id], match_id = match_id, save_flag = save_match_flag)
        
        # Retain match info
        if save_bulk_events: all_match_events += match_events
        if save_bulk_scoreboards: all_match_scoreboards += match_scoreboards

        # Update match ID tracker
        if not match_id_min or match_id < match_id_min:
            match_id_min = match_id
        if not match_id_max or match_id > match_id_max:
            match_id_max = match_id

    # Save match info
    if save_bulk_events: 
        save_bulk_info(matches_range_str = f'{match_id_min}-{match_id_max}',
                       info_to_save = all_match_events,
                       path_to_savefolder = 'events/bulk_matches')
    if save_bulk_scoreboards:
        save_bulk_info(matches_range_str = f'{match_id_min}-{match_id_max}',
                       info_to_save = all_match_scoreboards,
                       path_to_savefolder = 'scoreboards/bulk_matches')

    return all_match_events, all_match_scoreboards


def stream_matches(matches_json_file,
                   match_range = None,
                   save_match_flag = True,
                   save_bulk_events = False,
                   save_bulk_scoreboards = False,
                   report_flag = False,
                   report_every = 1):
    """
    Reads in matches using a generator by streaming in matches from the json file rather than reading in the entire
    json file at once. This is recommended for use with large datasets where you want control over how much data you
    are reading in and storing in memory. By default, match information will not be retained in memory, but each match
    will be saved individually.

    match_range: only reads matches that are within range
    save_match_flag: whether or not file will be created for match once read
    save_bulk_events: whether or not events will be retained in memory and eventually saved
    save_bulk_scoreboards: whether or not scoreboards will be retained in memory as they are read and eventually saved
    report_flag: whether or not report will be printed for match after reading
    report_every: how often (i.e. number of matches) reading will be reported
    """
    #start, end = [int(match_num) for match_num in re.findall(r"\d+", matches_json_file)]
    #matches_in_range = range(start, end)
    
    all_match_events = []
    all_match_scoreboards = []
    match_id_min = None
    match_id_max = None

    with open(matches_json_file, 'rb') as f:
        for match_id, match_info in ijson.kvitems(f, ""):
            # Check if match_id in match_range
            if match_range and (int(match_id) not in match_range):
                continue
            if report_flag and (int(match_id) % report_every == 0): print(f'Streaming in match {match_id}')

            # Get match information
            match_events, match_scoreboards = generate_match_info(match_json = match_info, match_id = match_id, save_flag = save_match_flag)

            # Retina match info
            if save_bulk_events: all_match_events += match_events
            if save_bulk_scoreboards: all_match_scoreboards += match_scoreboards

            # Update match ID tracker
            if not match_id_min or match_id < match_id_min:
                match_id_min = match_id
            if not match_id_max or match_id > match_id_max:
                match_id_max = match_id

    # Save match info
    if save_bulk_events:
        save_bulk_info(matches_range_str = f'{match_id_min}-{match_id_max}',
                       info_to_save = all_match_events,
                       path_to_savefolder = 'events/bulk_matches')
    if save_bulk_scoreboards:
        save_bulk_info(matches_range_str = f'{match_id_min}-{match_id_max}',
                       info_to_save = all_match_scoreboards,
                       path_to_savefolder = 'scoreboards/bulk_matches')

    return all_match_events, all_match_scoreboards


# Functions to add event and scoreboard info that requires full match to be read
def add_current_team_captures(match_events,
                              capture_event_name = 'capture'):
    """
    Adds the current score to each event in the list of events, returning the list of events and the final score

    Assumes all_player_events is sorted prior to function call
    """
    all_teams = set([event['team'] for event in match_events])
    team_caps = dict.fromkeys(all_teams, 0)

    for event in match_events:
        if event['event'] == capture_event_name:
            # Add 1 to the current team's score
            current_team = event['team']
            team_caps[current_team] += 1

        # Include the current scoreboard in the event
        for team in team_caps:
            event[f'score_team_{team}_current'] = team_caps[team]

    return match_events, team_caps


def add_current_teammates(match_events,
                          start_event_name = 'start',
                          join_event_name = 'join',
                          quit_event_name = 'quit',
                          switch_event_name = 'switch'):
    """
    TODO: Function is not currently working, as join events are not being detected. How to detect people otherwise? 
    What is someone does not perform any events?
    """
    all_teams = set([event['team'] for event in match_events])
    current_teams = dict.fromkeys(all_teams, [])

    for event in match_events:
        if event['event'] == start_event_name:
            current_teams[event['team']].append(event['name'])

    for event in match_events:
        if event['event'] == join_event_name:
            current_teams[event['new_team']].append(event['name'])
        elif event['event'] == quit_event_name:
            current_teams[event['old_team']].remove(event['name'])
        elif event['event'] == switch_event_name:
            current_teams[event['new_team']].append(event['name'])
            current_teams[event['old_team']].remove(event['name'])

        for team in current_teams:
            event[f'players_team_{team}_current'] = current_teams[team]

    return match_events


def add_winner_to_scoreboard(match_scoreboards, team_caps,
                             team_key = 'team'):
    """
    Returns scoreboard with winner added to the scoreboard. Requires all player scoreboards and sums of caps
    """
    for player in match_scoreboards:
        player_team = player[team_key]
        player['final_team_score'] = team_caps[player_team]

        all_scores_of_other_teams = [team_caps[team] for team in team_caps if team != player_team]
        player['win_loss'] = 1 if all(team_caps[player_team] > score for score in all_scores_of_other_teams) else 0.5 if all(team_caps[player_team] == score for score in all_scores_of_other_teams) else 0

    return match_scoreboards


def save_match_info(match_id, info_to_save, path_to_savefolder):
    """
    """
    savepath = Path.cwd().joinpath(path_to_savefolder).joinpath(f'{match_id}.csv')
    savepath.parent.mkdir(parents = True, exist_ok = True)

    all_events_df = pd.DataFrame.from_dict(info_to_save)
    all_events_df.to_csv(savepath, index = False)


def save_bulk_info(matches_range_str, info_to_save, path_to_savefolder):
    """
    """
    savepath = Path.cwd().joinpath(path_to_savefolder).joinpath(f'{matches_range_str}.csv')
    savepath.parent.mkdir(parents = True, exist_ok = True)

    all_events_df = pd.DataFrame.from_dict(info_to_save)
    all_events_df.to_csv(savepath, index = False)


if __name__ == '__main__':
    match_file = 'matches/bulkmatches3559932-3569932.json'

    all_player_events, all_player_scoreboards = read_matches(matches_json_file = match_file,
                                                             match_range = range(3559932, 3559934),
                                                             save_match_flag = False,
                                                             save_bulk_flag = True,
                                                             report_flag = True)

    """
    all_match_events, all_match_scoreboards = stream_matches(matches_json_file = match_file, 
                                                             match_range = range(3559932, 3559934),
                                                             save_match_flag = True,
                                                             save_bulk_flag = True, 
                                                             report_flag = True)
    """