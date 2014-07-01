
"""
Sample configuration

"""

import time


# RENAME THIS TO is_active() TO GET IT TO TAKE EFFECT
def _is_active():
    """
    Returns True if the sign should be currently active, False if it
    should sleep. This function isn't required to exist (sign will
    always be active).

    """
    t = time.localtime()
    # be active on weekdays bet 8am and 7pm
    return t.tm_wday >= 0 and t.tm_wday <=4 and \
        t.tm_hour >= 8 and t.tm_hour <= 19


def sign_sequence(ctx):
    """
    ctx = dict containing 'context' data from the sign script;
    currently, this just has the key 'message_queue' which is a Queue
    of msgs.

    sign_sequence() should return a dict representing a Sequence to be
    sent to the sign, containing the keys:

    'duration' : int duration to run the sequence, in seconds
    'messages' : a list of dicts describing a set of Messages to run

    A Message is a dict that consists of:

    'text'  : (required) str of text to display
    'mode'  : (optional) str indicating a mode (see alphasign docs).
              defaults to 'ROTATE'
    'speed' : (optional) str indicating speed (SPEED_1 to SPEED_5)
    'color' : (optional) str indicating a color (see alphasign docs)

    Python alphasign docs can be found here:

    https://alphasign.readthedocs.org/en/latest/
    """

    return { 'duration' : '60',
             'messages' : [ {
                'text' : 'Hello world',
                'mode' : 'HOLD' } ] }
