'''
This Class' responsibility is to manage a socket server being run for inferno.  It will have the ability to parse
incoming information and send it to the appropriate thread.  Additionally, it will take information generated by the
gps thread to determine if information needs to be sent back to the client.  All pathfinding calculations will be performed
by the server and sent to the client.

All incoming data will be checked for correctness
'''

import socket
import queue
from gps import GPS
from advancedgopigo3 import *
from grid import Grid
import select
import traceback
HOST = 'dante.local'
PORT = 10000

OPEN_SPACE = 0
OBSTACLE = 1
BORDER = 2


class Server:
    def __init__(self):

        # create the grid
        self.grid_width = 2.5
        self.grid_height = 3.5
        self.grid_x = 20
        self.grid_y = 28
        self.offset_x = 0
        self.offset_y = 0
        self.border_thickness = 2
        self.use_diagonals = True
        self.grid = Grid(self.grid_width, self.grid_height, self.grid_x, self.grid_y, self.offset_x, self.offset_y,
                         self.border_thickness, self.use_diagonals)
        
        # initialize default variables
        self.can_run = True
        self.rover_position = self.grid.get_node(0, 0)
        self.current_destination = None
        self.home = self.grid.get_node(0, 0)
        self.current_path = []
        self.simple_path = []

        # initialize the gpg
        self.gpg = AdvancedGoPiGo3(25)
        volt = self.gpg.volt()
        print("Current Voltage: ", volt)
        if volt < 9:
            print("Warning, voltage is getting low, impaired performance is expected.")
        elif volt < 8:
            print(
                "Critical! voltage is very low, please charge the batteries before continuing!  You have been warned!")

        # initialize send queue
        self.send_queue = queue.Queue()

        # initialize gps
        self.gps_queue = queue.Queue()
        self.gps = GPS(self.gps_queue, True, self.gpg, debug_mode=False)
        self.gps.minimum_distance = 100
        self.gps_can_run = True
        self.gps.set_obstacle_callback(self.obstacle_found)
        self.gps.set_position_callback(self.rover_position_change)
        self.gps.set_reached_point_callback(self.destination_reached)
        


        # start remote control thread
        self.remote_can_run = False

        # initialize the socket
        print("Awaiting connection")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        address = (HOST, PORT)
        self.socket.bind(address)
        self.socket.listen(1)

        # get the connection
        self.conn, self.addr = self.socket.accept()
        print("Successful connection from ", self.addr)
        self.gps.start()

    # this method manages incoming and outgoing commands
    def manage_commands(self):

        while self.can_run:
            # empty the queue and send all the data to the client
            while not self.send_queue.empty():
                data = self.send_queue.get_nowait()
                #print(data)
                self.conn.send(str.encode(data))
            # wait a moment for a response
            received, _, _ = select.select([self.conn], [], [], .1)

            # if not response then continue
            if received:
                data = self.conn.recv(1024).decode('utf-8').split()
                self.parse_data(data)

        # close up connections
        #self.socket.close()

    # this method parses the incoming data commands.  Each command comprises of atleast one letter that is removed from the list.
    def parse_data(self, data):

        while len(data) > 0:
            command = data.pop(0)
            print(command)
            # GUI INPUT COMMANDS
            # node has been changed
            if command == 'N':
                x = int(data.pop(0))
                y = int(data.pop(0))
                node_type = int(data.pop(0))
                self.grid.set_node(x, y, node_type)
                if node_type == OBSTACLE:
                    self.add_obstacle(self.grid.nodes[x][y])
                else:
                    self.find_path()

            # destination change
            elif command == 'D':
                x = int(data.pop(0))
                y = int(data.pop(0))
                if x == -1 and y == -1:
                    self.current_destination = None
                    self.current_path = []
                    self.simple_path = []
                    self.gpg.stop()
                    self.gps.cancel_early = True
                else:
                    node = self.grid.nodes[x][y]
                    if node.node_type == OPEN_SPACE and node != self.rover_position:
                        self.current_destination = node
                        self.find_path()

            # home change
            elif command == 'H':
                x = int(data.pop(0))
                y = int(data.pop(0))
                node = self.grid.nodes[x][y]
                if node.node_type == OPEN_SPACE:
                    self.home = node
            # go
            elif command == 'GO':
                self.gps_can_run = True
                self.next_gps_point()

            # REMOTE CONTROL COMMANDS
            # STOP
            elif command == 'S':
                self.gpg.stop()
                self.gps.cancel_early = True
            elif command == 'Q':
                print("Quitting")
                self.can_run = False
            # move
            elif command == 'M':
                # the numbers coming in should be integers, but aren't always
                x_speed = int(float(data.pop(0)))
                y_speed = int(float(data.pop(0)))

                # adjust to proper speeds
                if x_speed == 0 and y_speed == 0:
                    self.gpg.stop()
                elif x_speed == 0:
                    self.gpg.rotate_right_forever()
                elif y_speed == 0:
                    self.gpg.rotate_left_forever()
                else:
                    self.gpg.set_left_wheel(abs(x_speed))
                    self.gpg.set_right_wheel(abs(y_speed))
                    if y_speed > 25 and x_speed > 25:
                        self.gpg.backward()
                    else:
                        self.gpg.forward()
            # LEDS on
            elif command == "LON":
                print("Turning LED on")
                self.gpg.led_on(0)
                self.gpg.led_on(1)
                self.gpg.open_eyes()

            # LEDS off
            elif command == "LOFF":
                print("Turning LED off")
                self.gpg.led_off(0)
                self.gpg.led_off(1)
                self.gpg.close_eyes()

    def rover_position_change(self, position):
        # We only care about it if it is in the grid.
        if position.x > 0 and position.y > 0 and position.x <= self.grid_width and position.y <= self.grid_height:
            node = self.grid.node_from_global_coord(position)

            # we still only care if it moves it to a new node.
            if node != self.rover_position:
                print("callback-rover position changed")
                self.rover_position = node
                # if we arrived at a destination we are done.
                if self.current_destination is not None and node == self.current_destination:
                    self.current_destination = None
                    self.current_path = []

                # we need a new full route.
                self.current_path, _ = self.grid.find_path(self.rover_position, self.current_destination)
                self.send_path()

                # send info along
                self.send_message("R " + str(node.gridPos.x) + " "+ str(node.gridPos.y))

    def destination_reached(self,pos):
        print("callback-point reached",pos)

        # send the next point to the gps
        self.next_gps_point()

        # if we are at our final destination, we are done.
        if len(self.current_path) == 0 or len(self.simple_path) == 0 or (
                self.current_destination is not None and self.current_destination == self.rover_position):
            print("final destination reached")
            self.send_message("DR")
            self.gps_can_run = False

    def obstacle_found(self, position):
        # We only care about it if it is in the grid.
        if position.x > 0 and position.y > 0 and position.x <= self.grid_width and position.y <= self.grid_height:
            node = self.grid.node_from_global_coord(position)

            # We still only care if this changes anything
            if node.node_type != 1:
                print("callback-obstacle found")

                # we have an obstacle!
                self.add_obstacle(node)

    def add_obstacle(self, node, send_message=True):
        # if we have a valid node to work with.
        if node.node_type != 1 and node != self.rover_position:

            # we need to spread the grid.
            self.grid.set_node_type(node, 1)
            border = self.grid.all_borders
            self.send_message("N " + str(node.gridPos.x) + " " + str(node.gridPos.y) + " 1")
            print("N",node.gridPos.x,node.gridPos.y)

            # IF there is a destination in play and it is hit by the border, it needs to be cleared.
            if self.current_destination is not None:
                if border.__contains__(self.current_destination):
                    self.current_destination = None
                    self.current_path = []
                    self.simple_path = []
                else:
                    # we need a new path
                    self.find_path()
                    
                    # if we are currently in motion.  let's go!
                    if self.gps_can_run and len(self.simple_path) > 0:
                        destination = self.grid.get_global_coord_from_node(self.simple_path.pop(0))
                        self.gps_queue.queue.clear()
                        self.gps.cancel_early = True
                        self.gps_queue.put(destination)

    def find_path(self, send_message=True):
        if self.current_destination is not None:
            self.current_path, self.simple_path = self.grid.find_path(self.rover_position, self.current_destination)

            # send paths
            if send_message:
                self.send_path()
                self.send_simple_path()

    def next_gps_point(self):
        # if we actually have somewhere to go
        if len(self.simple_path) > 0:
            node = self.simple_path.pop(0)
            if node == self.rover_position and len(self.simple_path) > 0:
                node = self.simple_path.pop(0)
            destination = self.grid.get_global_coord_from_node(node)
            self.gps_queue.put(destination)
            self.send_simple_path()
        else:
            self.gps_can_run = False
            self.send_message("DR")

    def send_message(self, message):
        # puts a message in the send queue
        self.send_queue.put((" " + message))

    def send_path(self):
        # sends the full path to the client
        if len(self.current_path) >0:
            message = "FP "
            for p in self.current_path:
                message += p.__str__() + " "
            message += "D"
            self.send_message(message)

    def send_simple_path(self):
        # sends the simplified path to the client
        if len(self.simple_path) > 0:
            message = "SP "
            for p in self.simple_path:
                message += p.__str__() + " "
            message += "D"
            self.send_message(message)


if __name__ == "__main__":
    try:
        server = Server()
        server.manage_commands()
    except Exception as e:
        print(e)
        print(traceback.format_exc())
    finally:
        server.socket.shutdown(socket.SHUT_RDWR)
        server.socket.close()
        server.gps.thread_done = True
        server.gps.cancel_early = True
        server.gps.join()