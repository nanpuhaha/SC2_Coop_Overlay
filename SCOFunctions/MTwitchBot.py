import html
import time
import random
import difflib
import socket
import traceback
from datetime import datetime
import xml.etree.ElementTree as ET

from SCOFunctions.MFilePath import truePath
from SCOFunctions.SC2Dictionaries import UnitNameDict, Mutators
from SCOFunctions.MLogging import logclass

logger = logclass('TWITCH', 'INFO')
all_unit_ids = {u.lower() for u in set(UnitNameDict.keys())}
mutator_set = {m.lower() for m in set(Mutators.keys())}


class TwitchBot:
    def __init__(self, twdict, widget=None):
        self.channel = twdict['channel_name'].lower()
        self.bot_name = twdict['bot_name']
        self.bot_oauth = twdict['bot_oauth']
        self.host = twdict['host']
        self.port = int(twdict['port'])
        self.banks = twdict['bank_locations']
        self.responses = twdict['responses']
        self.greetings = twdict['greetings']
        self.banned_mutators = {m.lower() for m in twdict['banned_mutators']}
        self.banned_units = {u.lower() for u in twdict['banned_units']}
        self.widget = widget

        if len(self.banks) > 0:
            self.bank = self.banks.get('Default', list(self.banks.values())[0])

        self.commandNumber = random.randint(1, 1000000)
        self.UnconfirmedCommands = {}
        self.chat_log = truePath('ChatLog.txt')
        self.RUNNING = False

    def openSocket(self):
        self.s = socket.socket()
        self.s.connect((self.host, self.port))
        self.s.send(f"PASS {self.bot_oauth}\r\n".encode("utf-8"))
        self.s.send(f"NICK {self.bot_name}\r\n".encode("utf-8"))
        self.s.send(f"JOIN #{self.channel}\r\n".encode("utf-8"))

    def joinRoom(self):
        readbuffer_join = "".encode()
        Loading = True
        while Loading:
            try:
                readbuffer_join = self.s.recv(1024)
                readbuffer_join = readbuffer_join.decode()
                temp = readbuffer_join.split("\n")
                readbuffer_join = readbuffer_join.encode()
                readbuffer_join = temp.pop()

                for line in temp:
                    Loading = self.loadingComplete(line)
            except ConnectionResetError:
                logger.error('Twitch bot connection error. Trying again.')
                time.sleep(2)
                self.openSocket()
            except ConnectionAbortedError:
                logger.error('Twitch bot abort error. Trying again.')
                time.sleep(2)
                self.openSocket()
            except Exception:
                logger.error(traceback.format_exc())
                time.sleep(2)
                self.openSocket()

        logger.info("Bot has joined the chat")
        self.sendMessage('/color green')

    @staticmethod
    def loadingComplete(line):
        return "End of /NAMES list" not in line

    @staticmethod
    def getUser(line):
        separate = line.split(":", 2)
        try:
            return separate[1].split("!", 1)[0]
        except Exception:
            return None

    @staticmethod
    def getMessage(line):
        separate = line.split(":", 2)
        return separate[2]

    @staticmethod
    def console(line):
        return "PRIVMSG" not in line

    def sendMessage(self, message):
        messageTemp = f"PRIVMSG #{self.channel} :{message}"
        if '/color' not in message:
            logger.info(f"(sent: {message})")
        try:
            self.s.send(f"{messageTemp}\r\n".encode("utf-8"))
            if self.widget != None and '/color' not in message:
                self.widget.add_message(self.bot_name, message)
        except BrokenPipeError:
            logger.error('BrokenPipeError. Opening socket again')
            self.openSocket()
            self.s.send("{messageTemp}\r\n".encode("utf-8"))

    def sendGameMessage(self, ptype, message, user):
        """ Sends a message to the game """
        try:
            tree = ET.parse(self.bank)  #reload to account for new changes
            root = tree.getroot()

            # Reset unconfirmed commands if it's a new game
            for child in root:
                if child.attrib['name'] == "NewGame":
                    self.UnconfirmedCommands = {}
                    root.remove(child)
                    break

            # Update unconfirmed commands with those that were executed
            for child in root:
                if child.attrib['name'] == "ExecutedCommands":
                    for command in child:
                        com_number = command.attrib['name']
                        if com_number in self.UnconfirmedCommands:
                            del self.UnconfirmedCommands[com_number]

                    root.remove(child)  # Removes the section
                    break

            # Delete old commands if there are any
            for child in root:
                if child.attrib['name'] == 'Commands':
                    root.remove(child)
                    break

            # Get unconfirmed commands
            new_command_string = ""
            for command in self.UnconfirmedCommands:
                new_command_string = new_command_string + self.UnconfirmedCommands[command]

            # Get new commmand
            self.commandNumber += 1
            msg = message.replace('"', "''")
            msg = html.escape(msg)  # convert & → &amp;
            command_string = f'<Key name="{ptype} {self.commandNumber} #{user}"><Value string="{msg}"/></Key>'

            new_command_string = new_command_string + command_string
            self.UnconfirmedCommands[str(self.commandNumber)] = command_string

            # Create command section
            new_command_string = f'<Section name="Commands">{new_command_string}</Section>'
            root.append((ET.fromstring(new_command_string)))
            tree.write(self.bank)
            return ""

        except Exception:
            logger.error(f'Message failed to send\n{traceback.format_exc()}')

    def saveMessage(self, user, message):
        with open(self.chat_log, 'ab') as file:
            now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            log = f'{now}\t{user}\t{message.rstrip()}\n'.encode('utf-8')
            file.write(log)

    def run_bot(self):
        """ Initialize and runs the bot """
        try:
            self.openSocket()
            self.joinRoom()
            self.RUNNING = True
            self.pingsAndMessages()
        except Exception:
            logger.error(traceback.format_exc())

    def pingsAndMessages(self):
        """ Loop that manages twitch chat and sending messages to the game """
        GMActive = True
        GMActiveFull = False
        chatColor = 'green'
        chatColorError = 'red'
        CommandCooldown = 30
        UserCooldown = {}
        UsersToGreet = {u.lower() for u in set(self.greetings.keys())}
        recently_paused = False

        def user_on_cooldown(user):
            # Either no cooldown set, or user haven't started any cooldown
            if CommandCooldown <= 0 or user not in UserCooldown:
                return False
            elif (time.time() - UserCooldown[user]) < CommandCooldown:
                self.sendMessage(f'/color {chatColorError}')
                self.sendMessage(
                    f'/me Command not executed. {user} is on cooldown for the next {round(CommandCooldown - time.time() + UserCooldown[user],1)} seconds.'
                )
                return True
            else:
                return False

        while True:
            if not self.RUNNING:
                time.sleep(0.5)
                recently_paused = True
                continue

            try:
                readbuffer = self.s.recv(1024)
                readbuffer = readbuffer.decode()
                temp = readbuffer.split("\n")
                readbuffer = readbuffer.encode()
                readbuffer = temp.pop()

                # Prevent executing all commands that were issues while it was paused
                if recently_paused:
                    recently_paused = False
                    temp = ""

            except Exception:
                temp = ""

            for line in temp:
                if line == "":
                    break

                if "PING" in line and self.console(line):
                    msgg = "PONG :tmi.twitch.tv\r\n".encode()
                    self.s.send(msgg)
                    logger.info(msgg)
                    break

                # Get user, first and following words
                user = self.getUser(line)
                if user is None:
                    continue

                message = self.getMessage(line)
                first_word = message.split()[0].lower()
                self.saveMessage(user, message)
                if self.widget != None:
                    self.widget.add_message(user, message)
                try:
                    following_words = message.split(' ', 1)[1].rstrip()
                except Exception:
                    following_words = ''
                logger.info(f"{user}: {message.rstrip()}")

                # Commands
                if first_word == "!gm" and user == self.channel:
                    self.sendMessage(f'/color {chatColor}')
                    if 'full' in following_words:
                        GMActive = True
                        GMActiveFull = True
                        self.sendMessage('/me Full game integration. !mutator, !spawn, !wave and !resources commands enabled')
                    elif 'stop' in following_words:
                        GMActive = False
                        GMActiveFull = False
                        self.sendMessage('/me Game integration disabled!')
                    else:
                        GMActive = True
                        GMActiveFull = False
                        self.sendMessage('/me Partial game integration. !join and !message commands active')

                if first_word == "!cooldown" and user == self.channel:
                    try:
                        CommandCooldown = int(following_words)
                    except Exception:
                        CommandCooldown = 0
                    CommandCooldown = max(CommandCooldown, 0)
                    self.sendMessage(f'/color {chatColor}')
                    self.sendMessage(f'Cooldown for viewer commands set to {CommandCooldown} seconds')

                if first_word == "!bank" and user == self.channel:
                    if following_words in self.banks:
                        self.bank = self.banks[following_words]
                        self.sendMessage(f'/color {chatColor}')
                        self.sendMessage(f'/me Bank file changed to: {following_words}')
                    elif not following_words:
                        self.bank = self.banks.get('Default', list(self.banks.values())[0])
                        self.sendMessage(f'/color {chatColor}')
                        self.sendMessage('/me Bank file set to the default value')
                    else:
                        bank_keys = str(list(self.banks.keys()))[1:-1].replace("'", "")
                        self.sendMessage(f'/color {chatColorError}')
                        self.sendMessage(f'/me Incorrect bank name, choose one: {bank_keys}')

                if first_word == "!message":
                    self.sendMessage(f'/color {chatColor}')
                    if GMActive == False:
                        self.sendMessage(f'/color {chatColorError}')
                        self.sendMessage('/me Game integration inactive!')
                    else:
                        logger.info(f'Message sent: {user} {following_words}')
                        self.sendGameMessage('message', f'{user}: {following_words}', user)

                if first_word == "!mutator":
                    self.sendMessage(f'/color {chatColorError}')
                    if GMActiveFull == False:
                        self.sendMessage('/me Full game integration inactive!')
                    else:
                        mutator = following_words.lower().replace(' disable', '')

                        # Check if the name is correct
                        if mutator not in mutator_set:
                            possible_mutator_names = difflib.get_close_matches(mutator, mutator_set)
                            # If some matches, propose them
                            add_string = ""
                            if len(possible_mutator_names) > 0:
                                possible_mutator_names = {i[0].upper() + i[1:] for i in possible_mutator_names}
                                possible_mutator_names = str(possible_mutator_names)[1:-1].replace("'", "")
                                add_string = f'Did you mean: {possible_mutator_names}?'

                            self.sendMessage(f'/me Incorrect mutator name ({mutator})! {add_string}')

                        elif mutator.lower() in self.banned_mutators:
                            self.sendMessage('/me This mutator is banned from use and will not be activated!')

                        elif user_on_cooldown(user):
                            pass

                        else:
                            logger.info('Mutator enabled/disabled: {following_words}')
                            self.sendGameMessage('mutator', following_words, user)
                            UserCooldown[user] = time.time()

                if first_word == "!spawn":
                    self.sendMessage(f'/color {chatColorError}')
                    if GMActiveFull == False:
                        self.sendMessage('/me Full game integration inactive!')
                    else:
                        unit = following_words.split(' ')[0]
                        # Check if correct name
                        if unit.lower() not in all_unit_ids:
                            # Find similar unit ids
                            possible_unit_names = difflib.get_close_matches(unit, all_unit_ids)
                            add_string = ""
                            if len(possible_unit_names) > 0:
                                possible_unit_names = {i[0].upper() + i[1:] for i in possible_unit_names}
                                possible_unit_names = str(possible_unit_names)[1:-1].replace("'", "")
                                add_string = f'Did you mean: {possible_unit_names}?'
                            self.sendMessage(f'/me Incorrect unit name ({unit})! {add_string}')

                        elif unit.lower() in self.banned_units:
                            self.sendMessage(f'/me Spawning {unit} is prohibited!')

                        elif user_on_cooldown(user):
                            pass

                        else:
                            self.sendGameMessage('spawn', following_words, user)
                            UserCooldown[user] = time.time()
                            logger.info(f'Unit spawned: {following_words}')

                if first_word == "!resources":
                    self.sendMessage(f'/color {chatColorError}')
                    if GMActiveFull == False:
                        self.sendMessage('/me Full game integration inactive!')
                    elif user_on_cooldown(user):
                        pass
                    else:
                        self.sendGameMessage('resources', following_words, user)
                        UserCooldown[user] = time.time()
                        logger.info(f'Resources given by {user}: {following_words}')

                if first_word == "!join":
                    self.sendMessage(f'/color {chatColorError}')
                    if GMActive == False:
                        self.sendMessage('/me Game integration inactive!')
                    else:
                        self.sendGameMessage('join', following_words, user)
                        logger.info(f'User joined the game: {user}')

                if first_word == "!wave":
                    self.sendMessage(f'/color {chatColorError}')
                    if GMActiveFull == False:
                        self.sendMessage('/me Full game integration inactive!')
                    elif user_on_cooldown(user):
                        pass
                    else:
                        self.sendGameMessage('wave', following_words, user)
                        UserCooldown[user] = time.time()
                        logger.info(f'Wave sent by {user}: {following_words}')

                # General responses configurable in the settings
                if first_word[0] == "!" and first_word[1:] in self.responses.keys():
                    self.sendMessage(f'/color {chatColor}')
                    self.sendMessage(self.responses[first_word[1:]])

                # User greetings
                if user.lower() in UsersToGreet:
                    UsersToGreet.remove(user)
                    if user.lower() in self.greetings.keys():
                        self.sendMessage(f'/color {chatColor}')
                        self.sendMessage(self.greetings[user.lower()])
                    elif user in self.greetings.keys():
                        self.sendMessage(f'/color {chatColor}')
                        self.sendMessage(self.greetings[user])

            time.sleep(1)