import socket
import string
import struct
import sys
import time
from typing import List, Optional
import logging

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.WARN)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

class SOCBoardError(Exception):
    """Base exception for SOCBoard errors."""
    pass

class CommunicationError(SOCBoardError):
    """Raised when there's an error in UDP communication."""
    pass

class WriteVerificationError(SOCBoardError):
    """Raised when a write operation fails verification."""
    pass

class InvalidResponseError(SOCBoardError):
    """Raised when an invalid response is received from the board."""
    pass

class SOCBoard:
    def __init__(self, ip_address: str):
        self.ip_address = ip_address
        self.udp_port = 1240
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 128)
        self.socket.bind(('', self.udp_port))
        self.socket.setblocking(True)
        self.socket.settimeout(5)  # 5-second timeout

    def _send_udp_message(self, message: str) -> None:
        try:
            logger.info(f'Sending UDP message: "{message}"...')
            self.socket.sendto(message, (self.ip_address, self.udp_port))
            logger.info(f'Sent UDP message: "{message}"...')
        except socket.error as e:
            raise CommunicationError(f"Failed to send UDP message: {e}\0")

    def _receive_udp_message(self) -> str:
        try:
            data, addr = self.socket.recvfrom(1024)
            ret = data.decode()
            logger.info(f'Received UDP message: "{ret}"')
            return ret
        except socket.timeout:
            raise CommunicationError("UDP receive timeout")
        except socket.error as e:
            raise CommunicationError(f"Failed to receive UDP message: {e}\0")

    def _create_udp_message(self, register_space: int, command: str, register_address: int, value: int = 0) -> str:
        
        return str.encode(f"{{GAPI {register_space:02X} 2 {command} {register_address:02X} {value:04X}}}\0")

    def _parse_udp_response(self, response: str, expected_register_space: int, expected_register_address: int) -> int:
        parts = response.strip("{}\0").split()
        if len(parts) != 5 or "GAPI" not in parts[0] or parts[1] != f"{expected_register_space:02X}" or parts[2] != "1" or parts[3] != f"{expected_register_address:02X}":
            raise InvalidResponseError(f"Unexpected response format: {response}\0")
        processed_response = parts[4][:-1]
        if str.isdigit(processed_response):
            return int(processed_response)
        if all(c in string.hexdigits for c in processed_response):
            return int(processed_response, 16)
        return processed_response

    def read_register(self, register_space: int, register_address: int) -> int:
        message = self._create_udp_message(register_space, "R", register_address)
        self._send_udp_message(message)
        response = self._receive_udp_message()
        return self._parse_udp_response(response, register_space, register_address)

    def write_register(self, register_space: int, register_address: int, value: int, verify: bool = True) -> None:
        if verify:
            return self.verified_write_register(register_space, register_address, value)
        else:
            message = self._create_udp_message(register_space, "W", register_address, value)
            self._send_udp_message(message)

    def verified_write_register(self, register_space: int, register_address: int, value: int) -> None:
        message = self._create_udp_message(register_space, "V", register_address, value)
        self._send_udp_message(message)
        response = self._receive_udp_message()
        read_value = self._parse_udp_response(response, register_space, register_address)
        if read_value != value:
            raise WriteVerificationError(f"Write verification failed. Wrote {value:04X}, read back {read_value:04X}\0")
        return read_value

    @staticmethod
    def discover_boards(multicast_group = '239.255.255.1', timeout = 5) -> List['SOCBoard']:
        MULTICAST_TTL = 128
        ENABLE_HEARTBEAT_PORT = 1240
        HEARTBEAT_RESPONSE_PORT = 1270
        
        # Create a UDP socket for sending messages
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, MULTICAST_TTL)
        
        # Create UDP socket for response
        receive_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receive_sock.bind(('', HEARTBEAT_RESPONSE_PORT))
        receive_sock.settimeout(timeout)  # Listen for 5 seconds
        discovered_boards = []

        try:
            # Turn on heartbeat
            logger.info(f'Sending heartbeat activation...')
            message = b"{GAPI 00 2 W B0 1234}\0"
            send_sock.sendto(message, (multicast_group, ENABLE_HEARTBEAT_PORT))
            start_time = time.time()
            
            while time.time() - start_time < timeout:  # Listen for responses for 5 seconds
                try:
                    data, addr = receive_sock.recvfrom(1024)
                    dat = data.decode().strip('{}\r\n ').split()[-1]
                    logger.info(f'Got UDP message: "{data}" with payload {dat}')
                    if b'{BEAT' in data:
                        logger.info(f'Got Heartbeat from {addr[0]}')
                            # # Step 3: Turn off the heartbeat for this device
                            # stop_message = b'{GAPI 00 2 W B0 4321}'
                            # send_sock.sendto(stop_message, (addr[0], 1270))
                        board = SOCBoard(addr[0])
                        board._parse_board_info(int(dat, 16))
                        discovered_boards.append(board)
                        
                        # Turn off heartbeat for this board
                        board._turn_off_heartbeat()
                except socket.timeout:
                    continue
        
        except Exception as e:
            logging.critical(e, exc_info=True)

        finally:
            logger.info(f'Closing discovery socket...')
            send_sock.close()
            receive_sock.close()
        
        return discovered_boards

    def _turn_on_heartbeat(self) -> None:
        self.write_register(0, 0xB0, 0x1234)

    def _turn_off_heartbeat(self) -> None:
        self.write_register(0, 0xB0, 0x4321, False)

    def _parse_board_info(self, info: int) -> None:

        self.board_type = ["Neither", "Encoder", "Decoder", "Both"][(info & 0b11)] # Bits 1:0
        self.has_audio = bool((info >> 2) & 0b1) # Bit 2
        self.codec = ["MPEG2", "H264", "H265", "Reserved"][(info >> 3) & 0b111] # Bits 5:3
        self.precision = "10 Bits" if (info >> 6) & 0b1 else "8 Bits" # Bit 6
        self.fps = ["Up to 30", "Up to 60", "Up to 120", "Other"][(info >> 7) & 0b11] # Bits 8:7
        self.resolution = ["Up to 1080p", "Up to 4K", "Up to 8K", "Other"][(info >> 9) & 0b11] # Bits 10:9
        self.module_fpga = ["None", "Artix", "Zynq", "Arria10", "Reserved"][(info >> 11) & 0b111] # Bits 13:11
        self.channels = min(62, (info >> 14) & 0b111111) # Bits 19:14
        self.board_name = ["S1000", "VTR4000C", "VoIP-X", "VoIP-I", "Reserved"][(info >> 20) & 0b11111] # Bits 24:20


    def get_board_info(self) -> dict:
        """Return a dictionary containing all parsed board information."""
        return {
            "board_type": self.board_type,
            "has_audio": self.has_audio,
            "codec": self.codec,
            "precision": self.precision,
            "fps": self.fps,
            "resolution": self.resolution,
            "module_fpga": self.module_fpga,
            "channels": self.channels,
            "board_name": self.board_name
        }

    def __str__(self) -> str:
        """Return a string representation of the SOCBoard."""
        return f"SOCBoard({self.ip_address}) - {self.board_name} ({self.board_type})"

    def __repr__(self) -> str:
        """Return a string representation of the SOCBoard for debugging."""
        return self.__str__()
    
    '''
    HIGH LEVEL API
    '''

    def getBitrate(self):
        return self.read_register(0x01, 0x97)
    
    def setBitrate(self, bitrate, verify=False):
        return self.write_register(0x01, 0x97, bitrate, verify)
    
    def getChromaSubsampling(self):
        val = self.read_register(0x01, 0x03)
        match val:
            case 1:
                return "4:2:0"
            case 2:
                return "4:2:2"
            case _:
                return "Unknown"

def discover_and_print_boards(target = '239.255.255.1'):
    """Utility function to discover and print information about all boards on the network."""
    boards = SOCBoard.discover_boards(target)
    print(f"Discovered {len(boards)} SOC board(s):")
    for board in boards:
        print(f"\n{board}\0")
        info = board.get_board_info()
        for key, value in info.items():
            print(f"  {key}: {value}\0")
    return boards

if __name__ == "__main__":
    # Discover boards
    boards = SOCBoard.discover_boards()

    # Interact with a specific board
    if boards:
        board = boards[0]
        print(f"Interacting with board: {board}")
        
        print(f"Bitrate: {board.getBitrate()}")
        print("Changing bitrate...")
        print(f"New bitrate: {board.setBitrate(0xDAC, True)}")


