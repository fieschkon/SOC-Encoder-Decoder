import socket
import struct
import time
from typing import List, Optional

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
        self.socket.settimeout(2)  # 2-second timeout

    def _send_udp_message(self, message: str) -> None:
        try:
            self.socket.sendto(message.encode(), (self.ip_address, self.udp_port))
        except socket.error as e:
            raise CommunicationError(f"Failed to send UDP message: {e}")

    def _receive_udp_message(self) -> str:
        try:
            data, _ = self.socket.recvfrom(1024)
            return data.decode()
        except socket.timeout:
            raise CommunicationError("UDP receive timeout")
        except socket.error as e:
            raise CommunicationError(f"Failed to receive UDP message: {e}")

    def _create_udp_message(self, register_space: int, command: str, register_address: int, value: int = 0) -> str:
        return f"{{GAPI {register_space:02X} 2 {command} {register_address:02X} {value:04X}}}"

    def _parse_udp_response(self, response: str, expected_register_space: int, expected_register_address: int) -> int:
        parts = response.strip("{}").split()
        if len(parts) != 5 or parts[0] != "GAPI" or parts[1] != f"{expected_register_space:02X}" or parts[2] != "1" or parts[3] != f"{expected_register_address:02X}":
            raise InvalidResponseError(f"Unexpected response format: {response}")
        return int(parts[4], 16)

    def read_register(self, register_space: int, register_address: int) -> int:
        message = self._create_udp_message(register_space, "R", register_address)
        self._send_udp_message(message)
        response = self._receive_udp_message()
        return self._parse_udp_response(response, register_space, register_address)

    def write_register(self, register_space: int, register_address: int, value: int, verify: bool = True) -> None:
        if verify:
            self.verified_write_register(register_space, register_address, value)
        else:
            message = self._create_udp_message(register_space, "W", register_address, value)
            self._send_udp_message(message)
            self._receive_udp_message()  # Discard the response

    def verified_write_register(self, register_space: int, register_address: int, value: int) -> None:
        message = self._create_udp_message(register_space, "V", register_address, value)
        self._send_udp_message(message)
        response = self._receive_udp_message()
        read_value = self._parse_udp_response(response, register_space, register_address)
        if read_value != value:
            raise WriteVerificationError(f"Write verification failed. Wrote {value:04X}, read back {read_value:04X}")

    @staticmethod
    def discover_boards(multicast_group = '224.0.0.1') -> List['SOCBoard']:
        multicast_port = 1240
        discover_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        discover_socket.settimeout(2)
        
        try:
            # Turn on heartbeat
            message = "{GAPI 00 2 W B0 1234}"
            discover_socket.sendto(message.encode(), (multicast_group, multicast_port))
            
            discovered_boards = []
            start_time = time.time()
            
            while time.time() - start_time < 5:  # Listen for responses for 5 seconds
                try:
                    data, addr = discover_socket.recvfrom(1024)
                    response = data.decode()
                    
                    if response.startswith("{BEAT"):
                        board = SOCBoard(addr[0])
                        board._parse_board_info(response[10:18])
                        discovered_boards.append(board)
                        
                        # Turn off heartbeat for this board
                        board._turn_off_heartbeat()
                except socket.timeout:
                    continue
        
        finally:
            discover_socket.close()
        
        return discovered_boards

    def _turn_on_heartbeat(self) -> None:
        self.write_register(0, 0xB0, 0x1234)

    def _turn_off_heartbeat(self) -> None:
        self.write_register(0, 0xB0, 0x4321)

    def _parse_board_info(self, info: str) -> None:
        info_int = int(info, 16)
        
        self.board_type = ["Neither", "Encoder", "Decoder", "Both"][info_int & 0x3]
        self.has_audio = bool(info_int & 0x4)
        self.codec = ["MPEG2", "H264", "H265", "Reserved"][(info_int >> 3) & 0x7]
        self.precision = "10 Bits" if info_int & 0x40 else "8 Bits"
        self.fps = ["Up to 30", "Up to 60", "Up to 120", "Other"][(info_int >> 7) & 0x3]
        self.resolution = ["Up to 1080p", "Up to 4K", "Up to 8K", "Other"][(info_int >> 9) & 0x3]
        self.module_fpga = ["None", "Artix", "Zynq", "Arria10", "Reserved"][(info_int >> 11) & 0x7]
        self.channels = min(62, (info_int >> 14) & 0x3F)
        self.board_name = ["S1000", "VTR4000C", "VoIP-X", "VoIP-I", "Reserved"][(info_int >> 20) & 0x1F]

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

def discover_and_print_boards():
    """Utility function to discover and print information about all boards on the network."""
    boards = SOCBoard.discover_boards()
    print(f"Discovered {len(boards)} SOC board(s):")
    for board in boards:
        print(f"\n{board}")
        info = board.get_board_info()
        for key, value in info.items():
            print(f"  {key}: {value}")
    return boards

if __name__ == "__main__":
    # Discover boards
    boards = discover_and_print_boards()

    # Interact with a specific board
    if boards:
        board = boards[0]
        print(f"Interacting with board: {board}")
        
        # Read a register
        value = board.read_register(0x01, 0x97)
        print(f"Register 0x97 value: {value:04X}")

        # Write to a register with verification
        try:
            board.write_register(0x01, 0x97, 0xA0, verify=True)
            print("Write successful and verified")
        except WriteVerificationError as e:
            print(f"Write verification failed: {e}")

        # Get board info
        info = board.get_board_info()
        print("Board Information:")
        for key, value in info.items():
            print(f"  {key}: {value}")
