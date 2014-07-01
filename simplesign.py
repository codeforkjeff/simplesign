#!/usr/bin/python
"""

This is a simple application for BetaBrite LED signs, designed to:

- send a sequence of messages to the LED sign periodically

- allow users to provide content to be included in the sequence of
  messages, via a simple web interface

- allow users to pre-empt the next refresh with a 'high priority'
  message of their own

This code consists of:

- a main loop that repeatedly calls a function for data to send to the
  sign.

- a web server API that allows for queuing of messages, plus a simple
  HTML front end

"""

import BaseHTTPServer
import Queue
import glob
import imp
import json
import logging
from optparse import OptionParser
import os
import os.path
import sys
import threading
import time
import traceback

import alphasign

LOG = logging.getLogger(__name__)

SEQUENCE_QUEUE = Queue.Queue()
MESSAGE_QUEUE = Queue.Queue()

SHUTDOWN = False

# this is an arbitrarily high number < 93, which is the num of unique
# text file labels available. I don't know how high you can go before
# the sign runs out of memory.
NUM_TEXTFILES = 60


def get_mode(mode_str):
    return getattr(alphasign.modes, mode_str, None)


def get_color(color_str):
    return getattr(alphasign.colors, color_str, None)


def get_speed(speed_str):
    return getattr(alphasign.speeds, speed_str, None)


class HttpHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    """Might be cleaner to use something besides
    BaseHTTPRequestHandler?
    """

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        dispatch = (
            # urls: order matters here!
            ('/enqueue_sequence', self.enqueue_sequence),
            ('/enqueue_message', self.enqueue_message),
            ('/', self.frontend),
            )

        handled = False
        error = None
        for prefix, handler in dispatch:
            if self.path == prefix:
                try:
                    handler()
                except Exception as e:
                    error = e
                handled = True
                break
        if not handled:
            self.send_response(404)
        if error:
            LOG.error("Error occurred in handler: %s" % (str(error,)))
            self.send_response(500, "Error occurred in handler: %s" % (str(error,)))

    def _get_html(self, path):
        """Returns contents of file specified by 'path'. this caches.
        """
        if not hasattr(self, "_cache"):
            self._cache = {}

        if not path in self._cache:
            f = open(path)
            html = f.read()
            f.close()
            self._cache[path] = html

        return self._cache.get(path)

    def frontend(self):
        """Render an html page for a frontend, so user can queue stuff
        in a browser

        """
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        path = os.path.join(os.getcwd(),"frontend.html")
        self.wfile.write(self._get_html(path))

    def enqueue_sequence(self):
        """URL endpoint for queueing a sequence of messages, to be run
        asap
        """
        if self.command == 'POST':
            length = int(self.headers.getheader('content-length'))
            postdata = self.rfile.read(length)
            seq = json.loads(postdata)
            if "duration" in seq and "messages" in seq:
                messages = seq['messages']
                LOG.info("Queuing sequence containing messages: " + ", ".join([m.get('text') for m in messages]))
                SEQUENCE_QUEUE.put(seq)
                self.send_response(200)
            else:
                self.send_response(500, "JSON object didn't contain 'duration' and 'messages' keys")
        else:
            self.send_response(500, "GET not supported")

    def enqueue_message(self):
        """URL endpoint for queueing a single message, to be included
        in the default sequence of messages
        """
        if self.command == 'POST':
            length = int(self.headers.getheader('content-length'))
            postdata = self.rfile.read(length)
            msg = json.loads(postdata)
            if "text" in msg:
                LOG.info("Queuing message: " + msg['text'])
                MESSAGE_QUEUE.put(msg)
                self.send_response(200)
            else:
                self.send_response(500, "JSON object didn't contain 'text' key")
        else:
            self.send_response(500, "GET not supported")


def start_server(port):
    """Start a web server on specified port in a separate thread, and
    also start a monitor thread for SHUTDOWN signal
    """
    server = None
    def listen_for_shutdown():
        """ monitor thread for SHUTDOWN """
        while not SHUTDOWN:
            time.sleep(1)
        LOG.info("Shutting down web server...")
        server.shutdown()

    threading.Thread(target=listen_for_shutdown).start()

    LOG.info("Starting server on port %d" % (port,))
    server = BaseHTTPServer.HTTPServer(('', port), HttpHandler)
    server.serve_forever(poll_interval=1)


def display_message(sign, msg, textfile, log=True):
    """This translates what we call a 'msg' into the sign's textfile
    and stringfile concepts. For simplicity, messages can only have
    one color, speed and mode (even though the protocol allows more
    flexibility) and these are split across a pair of
    textfile/stringfile objects.
    """

    if log:
        LOG.info("Displaying msg: %s" % (msg['text'],))

    text = ""
    if "color" in msg:
        text += "%s" % (get_color(msg['color'],))
    if "speed" in msg:
        text += "%s" % (get_speed(msg['speed'],))
    text += msg['text']

    # default mode
    mode = 'ROTATE'
    if 'mode' in msg:
        mode = msg['mode']

    # only write if something changed
    if textfile.mode != get_mode(mode) or textfile.data != text:
        LOG.debug("textfile changed, writing to sign")
        textfile.mode = get_mode(mode)
        textfile.data = text
        sign.write(textfile)


def check_if_active(currently_active, active_fn, sign, textfiles):
    """Returns bool for new active status; when switching to inactive
    mode, clear out the sign. """
    # sleep and then skip to next iteration if not active
    try:
        if not active_fn():
            if currently_active:
                LOG.info("Going into inactive mode, sleeping...")
                # clear sign while inactive
                for t in textfiles:
                    display_message(sign, { 'mode' : t.mode, 'text' : '' }, t)
            time.sleep(1)
            return False
    except Exception as e:
        LOG.error("Error in is_active(): %s" % (str(e),))

    if not currently_active:
        LOG.info("Waking up from inactive mode.")
    return True


def sleep_for(sleeptime):
    LOG.info("Sleeping for %d secs..." % (sleeptime,))
    start = time.time()
    # only sleep for 1s at a time so we can respond to stuff
    while time.time() - start < sleeptime \
            and not SHUTDOWN \
            and SEQUENCE_QUEUE.empty():
        time.sleep(1)
    LOG.info("Woke up!")


def sign_loop(sign, module):
    """Main worker loop that feeds sequences to the sign.
    """
    # there are 93 valid labels (see p. 50 of docs)
    valid_labels = iter([chr(x) for x in range(0x20, 0x7E + 1) if x != 0x30 and x != 0x3F])

    textfiles = []
    for i in range(NUM_TEXTFILES):
        textfiles.append(alphasign.Text("",
                                        size=125,
                                        label="%s" % (valid_labels.next(),),
                                        mode=get_mode("HOLD")))

    sign.allocate(textfiles)

    run_sequence = textfiles[0:1]

    sign.set_run_sequence(run_sequence)

    for t in textfiles:
        sign.write(t)

    is_active = getattr(module, "is_active", lambda: True)

    active = True

    while not SHUTDOWN:
        try:
            # sleep and then skip to next iteration if not active
            active = check_if_active(active, is_active, sign, textfiles)
            if not active:
                continue

            try:
                sequence = SEQUENCE_QUEUE.get(True, 1)
            except Queue.Empty:
                sequence = None

            ctx = { 'message_queue' : MESSAGE_QUEUE }
            if not sequence:
                try:
                    sequence = module.sign_sequence(ctx)
                except Exception as e:
                    LOG.error("Error running sign_sequence(): %s" % (str(e),))
                if not sequence:
                    sequence = {}

            sleeptime = 60

            messages = []
            if sequence:
                messages = sequence.get('messages', [])
                num_msgs = len(messages)
                if num_msgs > NUM_TEXTFILES:
                    LOG.info("WARNING: Got %d messages, which exceeds limit of %d. Truncating." % (len(messages), NUM_TEXTFILES))
                # modify run_sequence if necessary
                if num_msgs != len(run_sequence):
                    LOG.debug("Re-setting run sequence")
                    run_sequence = textfiles[0:num_msgs]
                    sign.set_run_sequence(run_sequence)

                sleeptime = int(sequence.get('duration', 60))

            i = 0
            for textfile in textfiles:
                log = True
                if i < len(messages):
                    msg = messages[i]
                else:
                    msg = { 'mode' : 'HOLD', 'text' : '' }
                    log = False
                display_message(sign, msg, textfile, log=log)
                i += 1

            # let it display for given duration
            sleep_for(sleeptime)

        except KeyboardInterrupt:
            LOG.info("Stopping...")
            keep_going = False

    LOG.info("Exiting sign loop")


def guess_device():
    """Returns best candidate for tty devices to use
    """
    for path in ("/dev/ttyS0", "/dev/tty.usbserial"):
        if os.path.exists(path):
            return path

    # http://www.xbsd.nl/2011/07/pl2303-serial-usb-on-osx-lion.html
    # This driver, which worked for my Mac Mini, creates these tty files
    candidates = glob.glob("/dev/tty.PL2303*")
    if len(candidates) > 0:
        return candidates[0]

    return None


def main():
    """Main function
    """
    parser = OptionParser("%prog")
    parser.add_option("-d", "--device",
                      help="serial/USB device to use",
                      action="store",
                      type="string",
                      dest="device",
                      default=None)
    parser.add_option("-m", "--module",
                      help="module to load for a sign_sequence() function",
                      action="store",
                      type="string",
                      dest="module",
                      default="sample")
    parser.add_option("-p", "--port",
                      help="port to use for web service",
                      action="store",
                      type="string",
                      dest="port",
                      default="8000")
    parser.add_option("-v", "--verbose",
                      help="turn on verbose messages for debugging",
                      action="store_true",
                      dest="verbose",
                      default=False)

    (options, args) = parser.parse_args()

    level = logging.INFO
    if options.verbose:
        level = logging.DEBUG
    logging.basicConfig(format='%(asctime)s:%(levelname)s:%(message)s', level=level)

    os.chdir(os.path.dirname(os.path.realpath(__file__)))

    device = options.device
    if not device:
        device = guess_device()

    module = None
    try:
        results = imp.find_module(options.module)
        args = (options.module,) + results
        module = imp.load_module(*args)
        LOG.info("Module '%s' loaded successfully" % (options.module,))
    except ImportError as e:
        LOG.error("ERROR: Could not find module '%s': %s" % (options.module, str(e)))
        sys.exit(1)
    if getattr(module, 'sign_sequence', None) is None:
        LOG.error("ERROR: Module '%s' has no function sign_sequence()" % (options.module,))
        sys.exit(1)

    LOG.info("Initializing sign at %s..." % (device,))
    sign = alphasign.Serial(device=device)
    sign.connect()
    sign.debug = False
    sign.clear_memory()

    threading.Thread(target=start_server, args=(int(options.port),)).start()

    LOG.info("Starting sign loop thread...")
    threading.Thread(target=sign_loop, args=(sign, module)).start()

    try:
        time.sleep(2)
        LOG.info("Okay, everything's been started. Hit Ctrl-C to exit...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    LOG.info("Shutting everything down, this may take a little while, hang on...")

    global SHUTDOWN
    SHUTDOWN = True


if __name__ == "__main__":
    main()
