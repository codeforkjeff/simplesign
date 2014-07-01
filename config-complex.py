
"""

Sample complex configuration file that creates messages from a number
of different sources, including buildbot status (which we use for
continuous integration to track breakage), weather.com, news sources,
The Onion, bugzilla 'quips', etc.

This is more or less the config file used at my workplace, but I've
blanked out URLs and hostnames, so you will need to adapt this code
for your own purposes.

"""

import datetime
import Queue
import logging
import os.path
import random
import time
import traceback
import urllib
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests


LOG = logging.getLogger(__name__)

VITAL_STATS = {}

# fixed-length pool of messages
POOL = []

POOLSIZE = 4

def random_ints(n, _max):
    """
    Returns an n-sized list of unique random ints up to but not
    including _max
    """
    ints = set()

    if n > _max:
        raise Exception("impossible to return %d unique ints where 0 <= n <= %d" % (n, _max - 1))
    while len(ints) < n:
        ints.add(random.randint(0, _max - 1))
    return list(ints)


def random_from_list(n, _list):
    return [_list[i] for i in random_ints(n, len(_list))]


def support_random(f):
    """
    Decorator: returns fn that accepts 'random' arg specifying how
    many random things to return from results of wrapped function
    """
    def wrapper(*args, **kwargs):
        random_arg = None
        if "random" in kwargs:
            random_arg = kwargs['random']
            del kwargs['random']

        msgs = f(*args, **kwargs)

        if random_arg:
            return random_from_list(random_arg, msgs)
        return msgs
    return wrapper


def make_messages(**outer_kwargs):
    """
    Decorator fn that transforms output of lists of strings to message
    dicts, doing data cleaning and filtering, and handling uncaught
    exceptions by logging them and returning an empty list. The
    wrapper fn supports randomizing as well (see support_random)
    """
    mode = outer_kwargs.get('mode', 'ROTATE')
    color = outer_kwargs.get('color', 'RED')
    speed = outer_kwargs.get('speed', 'SPEED_1')

    def real_decorator(function):

        @support_random
        def wrapper(*args, **kwargs):
            try:
                results = function(*args, **kwargs)
            except Exception as e:
                LOG.error("Error running %s: %s" % (function.__name__, str(e)))
                LOG.error(traceback.format_exc())
                return []

            msgs = [{ 'text' : i, 'mode' : mode, 'speed' : speed, 'color' : color } for i in results]

            # clean
            def normalize_text_in_dict(d):
                d['text'] = normalize(d['text'])
                return d

            msgs = [normalize_text_in_dict(m) for m in msgs]

            # filter
            return filter_msgs(msgs)

        return wrapper

    return real_decorator


def filter_(msg):
    """
    Returns False if msg is too long or has unprintable (unicode) chars
    """
    if len(msg['text']) > 125:
        return False
    try:
        msg['text'].decode('ascii')
    except UnicodeEncodeError:
        try:
            LOG.info("Couldn't decode into ascii, filtering out: %s" % (msg['text'],))
        except:
            LOG.info("Couldn't decode a message (undisplayable) into ascii, filtering out.")
        return False
    return True


def filter_msgs(msgs):
    return [m for m in msgs if filter_(m)]


def normalize(s):
    """ replace weird unicode chars with ascii pseudo-equivalents """
    # There should be a lot more here
    s = s.replace(u"\u2019", "'")
    s = s.replace(u"\u2013", "-")  # en dash
    s = s.replace(u"\u2014", "-")  # em dash
    s = s.replace(u'\xe1', "a")    # a with acute accent
    return s


class Cache(object):

    def __init__(self, cache_time):
        self.cache_time = cache_time
        self.cache = {}
        self.last_updated = {}

    def __call__(self, func):

        def wrapper(*args, **kwargs):
            self.expire_cache()

            key = str(args) + str(kwargs)
            last_updated = self.last_updated.get(key, 0)
            time_diff = time.time() - last_updated

            if (time_diff > self.cache_time) or key not in self.cache:
                LOG.info("%s cache miss" % (func.__name__,))
                self.cache[key] = func(*args, **kwargs)
                self.last_updated[key] = time.time()
            else:
                LOG.info("%s cache hit" % (func.__name__,))

            return self.cache[key]

        return wrapper

    def expire_cache(self):
        """ clear out old data """
        for key in self.last_updated.keys():
            last_updated = self.last_updated[key]
            time_diff = int(time.time() - last_updated)
            if time_diff > self.cache_time:
                del self.last_updated[key]
                del self.cache[key]


@Cache(3600)
def cached_fetch(url):
    """ caching wrapper around requests.get() """
    r = requests.get(url)
    return r.text


def tweets(twitter_name):
    text = cached_fetch("https://twitter.com/%s" % (twitter_name,))

    bs = BeautifulSoup(text)

    #contents = [div for div in bs.find_all("div") if "content" in div.get('class','')]

    tweets = [p.text for p in bs.find_all("p") if "tweet-text" in p.get('class','')]
    return tweets

QUIPS = []

@make_messages()
def quips():
    # quips.html is the output from our bugzilla server, which we
    # manually refresh as a disk file periodically, because bugzilla
    # requires authentication, so we can't hit the page directly.
    global QUIPS
    if len(QUIPS) == 0 and os.path.exists("quips.html"):
        f = open("quips.html")
        contents = f.read()
        f.close()

        bs = BeautifulSoup(contents)

        # should be third table
        table = bs.find_all('table')[2]

        rows = table.find_all('tr')[1:]

        # def row_author(row):
        #     return row.find_all("td")[1].text.strip()
        def row_quip(row):
            return row.find("td").text.strip()

        QUIPS = [row_quip(row) for row in rows]

    return QUIPS


def parse_blamelist(build_url):
    r = requests.get(build_url)
    html = r.text
    bs = BeautifulSoup(html)

    # kinda fragile!

    # find the h2 tag containing blamelist
    h2 = [h2 for h2 in bs.find_all("h2") if h2.text == 'Blamelist:'][0]
    # after h2 is a text node, and then OL node
    ol = h2.next_sibling.next_sibling
    blamelist = [li.text for li in ol.find_all("li")]
    return blamelist


@make_messages(color='RED', mode='HOLD')
def buildbot():
    builders = [
        'http://BUILDBOTSERVER/SOME_BUILDER_1',
        'http://BUILDBOTSERVER/SOME_BUILDER_2',
        'http://BUILDBOTSERVER/SOME_BUILDER_3',
        ]

    text = "Buildbot OK."
    for builder in builders:
        test_suite = builder[builder.rindex('/')+1:]
        try:
            r = requests.get(builder)
        except Exception as e:
            text = "Buildbot FAIL"
        html = r.text
        bs = BeautifulSoup(html)

        # doc structure is slightly diff when test is in progress
        if "Currently Building" in html:
            last_build = bs.find_all("ul")[1].find("li")
        else:
            last_build = bs.find("ul").find("li")

        if "failure" in str(last_build):
            link_to_build = last_build.find("a")["href"]
            link_to_build = "http://BUILDBOTSERVER" + link_to_build[2:]
            blamelist = parse_blamelist(link_to_build)

            whodunnit = "Whodunnit?! " + " ".join([suspect + "?" for suspect in blamelist])

            date = str(last_build.find("font").contents[0])
            date_str = date[1:-1] # trim off parens
            text = "Buildbot FAIL! in %s at %s %s" % (test_suite, date_str, whodunnit)

    return [ text ]


@make_messages()
def nytimes():
    text = cached_fetch("http://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml")
    rss = text.encode('UTF-8')

    # date format: "08 Dec 2013"
    today = time.strftime("%d %b %Y")

    root = ET.fromstring(rss)
    items = [e for e in root.findall(".//item") if today in e.find("pubDate").text]

    return [i.find("title").text for i in items]


@make_messages()
def philly_dot_com():
    text = cached_fetch("http://www.philly.com/philly_news.rss")
    rss = text.encode('UTF-8')
    root = ET.fromstring(rss)

    items = [e for e in root.findall(".//item")]

    def myfilter(msg):
        if msg.startswith("VIDEO:"):
            return False
        return True

    return [m for m in [i.find("title").text for i in items] if myfilter(m)]


@make_messages()
def breakingnews():
    """ headlines from breakingnews.com """
    messages = []

    text = cached_fetch("http://api.breakingnews.com/api/v1/item/?format=rss")
    rss = text.encode('UTF-8')
    root = ET.fromstring(rss)
    entries = [e for e in root.findall(".//{http://www.w3.org/2005/Atom}entry")]

    i = 0
    while i < 10 and i < len(entries):
        headline = entries[i].find("{http://www.w3.org/2005/Atom}title").text
        pos_dash = headline.rfind("-")
        if pos_dash != -1:
            headline = headline[:pos_dash].strip()
        messages.append(headline)
        i += 1

    def myfilter(msg):
        if msg.startswith("Photo:"):
            return False
        return True

    return [m for m in messages if myfilter(m)]


@make_messages()
def democracynow():
    text = cached_fetch("http://www.democracynow.org/democracynow.rss")
    rss = text.encode('UTF-8')
    root = ET.fromstring(rss)

    items = [e for e in root.findall(".//item")]
    items = [i for i in items if i.find("title").text.startswith("Headlines")]
    i = items[0]

    # they store a chunk of html in here containing all headlines in UL tree
    html = i.find("{http://purl.org/rss/1.0/modules/content/}encoded").text

    bs = BeautifulSoup(html)

    headlines = [link.text for link in bs.find_all("a")]

    return headlines


@support_random
def news():
    return breakingnews() + philly_dot_com() + democracynow()


@make_messages()
def onion():
    text = cached_fetch("http://feeds.theonion.com/theonion/daily")
    rss = text.encode('UTF-8')

    root = ET.fromstring(rss)

    def myfilter(title):
        if ":" in title or "[" in title or "The Onion" in title:
            return False
        return True

    items = [item for item in root.findall(".//item") if myfilter(item.find("title").text)]
    return [item.find("title").text for item in items]


@make_messages()
def colbert():

    tweets = tweets("StephenAtHome")

    def myfilter(t):
        if t.startswith('TONIGHT') or "@ColbertReport" in t or "#" in t:
            return False
        return True

    return [t + " (Stephen Colbert)" for t in
             [t for t in tweets if myfilter(t)]]


@make_messages()
def tiny_words():
    """ micropoetry from tinywords.com """
    text = cached_fetch('http://tinywords.com/feed/')

    root = ET.fromstring(r.text)

    latest = root.findall(".//item/description")[0].text
    latest_by = root.findall(".//item/{http://purl.org/dc/elements/1.1/}creator")[0].text
    latest = latest.replace('&#8230;', '...')

    msg = latest + " --" + latest_by

    # TODO: unfinished
    return []


@make_messages(color='GREEN')
def more_quotes():
    return [ "Just what do you think you're doing, Dave?",
             "Now THAT'S a solution for progress!",
             ]


@make_messages(color='GREEN', mode='ROTATE')
def weather():
    text = cached_fetch("http://www.wunderground.com/cgi-bin/findweather/getForecast?query=39.943%2C-75.172&sp=KPAPHILA35")
    bs = BeautifulSoup(text)

    current_temp = int(float(bs.find(id='rapidtemp')["value"]))
    feels_like = int(float(bs.find(id='tempFeel').find(class_='b').text))
    current_conditions = bs.find(id='curCond').text

    msgs = [ "%d deg & %s" % (current_temp, current_conditions) ]
    if feels_like - current_temp >= 2:
        msgs.append("Feels like %d deg" % (feels_like,))

    return msgs


def is_active():
    t = time.localtime()

    #return t.tm_wday >= 0 and t.tm_wday <= 4 and \
    #    t.tm_hour >= 8 and t.tm_hour <= 19

    # only active bet 8am and 8pm
    return t.tm_hour >= 8 and t.tm_hour <= 19


@make_messages(color='RED', mode='HOLD')
def commits():
    # we access info about commits made to code repository via viewvc
    now = time.gmtime()

    mindate = urllib.quote_plus(time.strftime("%Y-%m-%d 00:00:01", now))
    maxdate = urllib.quote_plus(time.strftime("%Y-%m-%d %H:%M:00", now))

    url = "http://VIEWVC_SERVER" % (mindate, maxdate)

    rss = cached_fetch(url)

    root = ET.fromstring(rss)
    items = [e for e in root.findall(".//item")]
    num_commits = len(items)

    if num_commits == 0:
        return []

    return [ "%d commits today." % (num_commits,) ]


def time_now():
    """ Returns str repr of of current time (ie. "3:45pm") """
    now = datetime.datetime.now()
    hour = now.strftime("%I")
    if hour.startswith("0"):
        hour = hour[1:]
    minute = now.strftime("%M")
    ampm = now.strftime("%p").lower()
    return hour + ":" + minute + ampm

def system_stats():
    """
    System status messages
    """
    messages = []

    messages.append({ 'text' : 'Status ' + time_now(), 'mode' : 'HOLD', 'speed' : 'SPEED_1' })

    status_functions = [
        buildbot,
        commits,
        ]

    for fn in status_functions:
        ret_val = None
        try:
            ret_val = fn()
        except Exception as e:
            LOG.error("ERROR in sign_sequence running %s: %s" % (fn.__name__, str(e)))
        if ret_val:
            messages.extend(ret_val)

    return messages


def messages_in_pool(message_queue):
    """
    Move messages from message_queue into POOL and trim it per FIFO
    """
    two_hours = 7200

    # clear out old msgs: TODO: instead of using set 2 hrs, use a time
    # duration stored in the msg
    # iterating over shallow copy is important
    for item in POOL[:]:
        if time.time() - item[0] >= two_hours:
            POOL.remove(item)

    stop = False
    while not stop:
        # move stuff from message_queue into our pool
        try:
            message = message_queue.get(True, 1)
            POOL.append((time.time(), message))
        except Queue.Empty:
            stop = True

    # newer msgs bump older ones out of the pool
    while len(POOL) > POOLSIZE:
        POOL.pop(0)

    return [i[1] for i in POOL]


@make_messages()
def weekend():
    msgs = []
    t = time.localtime()
    weekend_day = None
    if t.tm_wday == 5:
        weekend_day = "SATURDAY"
    if t.tm_wday == 6:
        weekend_day = "SUNDAY"
    if weekend_day:
        msgs.append("It's %s! What are you doing here?!" % (weekend_day,))
    return msgs


def fun_stuff(n, message_queue):
    """Return all messages in POOL, OR if that's less than n,
    supplement with msgs from other fun sources
    """
    # all messages from pool, even if this exceeds n
    funstuff = messages_in_pool(message_queue)

    # if we don't have enough fun stuff, add more
    if len(funstuff) < n:
        fill = n - len(funstuff)
        candidates = onion() + more_quotes()
        funstuff.extend(random_from_list(fill, candidates))

    funstuff += weekend()

    return funstuff


def interleave_pauses(messages):
    result = []
    pause = { 'text' : ' ' * 15, 'mode' : 'ROTATE', 'speed' : 'SPEED_1' }
    for i in range(len(messages)):
        result.append(messages[i])
        result.append(pause)
    return result


def sign_sequence(ctx):
    stats = system_stats()

    pause = { 'text' : ' ' * 10, 'mode' : 'HOLD', 'speed' : 'SPEED_1' }

    # our sequence: we do some shenanigans here to time msgs and
    # pauses to improve readability on the sign
    messages = stats + [ pause ] + weather() + [ pause ] + \
        interleave_pauses(quips(random=4)) + \
        stats + [ pause ] + interleave_pauses(news(random=4)) + \
        stats + [ pause ] + interleave_pauses(fun_stuff(4, ctx['message_queue']))

    five_mins = 60 * 5
    return { 'duration' : five_mins, 'messages' : messages }
