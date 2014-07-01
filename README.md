simplesign
==========

Simple application that periodically displays messages on a BetaBrite LED sign 


Requirements
------------

You will need to install the [alphasign library](https://github.com/msparks/alphasign), which implements the protocol to communicate with the LED sign.

If you use and adapt the 'complex' configuration file, you will need the [BeautifulSoup](http://www.crummy.com/software/BeautifulSoup/) and [requests](http://docs.python-requests.org/en/latest/) libraries.

I've only used this with a BetaBrite Classic, so I don't know how well it works with any other models.

Running the Code
----------------

After cloning this repo, run:
    
    ./run.sh

This will start the app in the background and try to communicate with the sign using one of the standard, popular serial/usb devices, displaying "Hello World." It will also start a web server you can use to send messages to the sign, which you access at the following address in your browser:

    http://localhost:8000/
    
Check the simplesign.log file if the app fails to start.

Sign Configuration Files
------------------------

Config files are just regular Python modules. They need to define a single function, called sign_sequence(), which takes a single argument, a dictionary containing some context information. (Currently, the only thing in this dict are the messages entered via the web interface, but more things may be added in the future).

sign_sequence() should return a dict representing a sequence of messages to display. It should contain two key/value pairs: 'duration', whose value is an int specifying the duration of the sequence in seconds, and 'messages', a list of dicts each describing a message to display.

config-sample.py contains the bare bones "hello world" example to demonstrate the data structure that config file should return

config-complex.py creates messages from a number of different sources, including buildbot status (which we use for continuous integration to track breakage), weather.com, news sources, The Onion, bugzilla 'quips', etc. This is more or less the config file used at my workplace, but I've blanked out URLs and hostnames, so you will need to adapt the code
for your own purposes.

To run a particular configuration, use the -m option:

    ./simplesign.py -m config-sample

