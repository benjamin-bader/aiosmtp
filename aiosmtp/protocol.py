"""SMTP server."""

__all__ = ['SmtpProtocol']

import asyncio
import logging

from email._header_value_parser import get_addr_spec, get_angle_addr

from . import const
from . import errors
from . import streams

ACCESS_LOG = logging.getLogger("smtp.access")

STATE_CONNECTING = 0
STATE_OPEN = 1
STATE_CLOSED = 2

READ_MODE_COMMAND = 0
READ_MODE_DATA = 1


class SmtpProtocol(asyncio.StreamReaderProtocol):
    def __init__(self, handler, host=None, loop=None, fqdn=b'localhost',
                 max_size=None):
        super().__init__(streams.SmtpStreamReader(loop=loop),
                         self.client_connected, loop=loop)

        self.connection_open = asyncio.Future(loop=loop)
        self.connection_closed = asyncio.Future(loop=loop)

        self.handler = handler
        self._fqdn = fqdn
        self._max_size = max_size
        self._state = STATE_CONNECTING
        self._loop = loop

        self._read_mode = READ_MODE_COMMAND
        self._helo = None
        self._sender = None
        self._recipients = []
        self._allowed_recipients = set()
        self._is_esmtp = False
        self._max_size = None
        self._message_size = None
        self._recipients_truncated = False

        self.COMMANDS = {
            b'HELO': SmtpProtocol.helo,
            b'EHLO': SmtpProtocol.ehlo,
            b'VRFY': SmtpProtocol.vrfy,
            b'EXPN': SmtpProtocol.expn,
            b'RSET': SmtpProtocol.rset,
            b'MAIL': SmtpProtocol.mail,
            b'RCPT': SmtpProtocol.rcpt,
            b'DATA': SmtpProtocol.data,
            b'NOOP': SmtpProtocol.noop,
            b'QUIT': SmtpProtocol.quit,
        }

        self.worker = asyncio.async(self.run(), loop=loop)

        if self.is_open():
            self.connection_open.set_result(True)

    def reset_state(self):
        self._read_mode = READ_MODE_COMMAND
        self._helo = None
        self._sender = None
        self._recipients = []
        self._allowed_recipients = set()
        self._is_esmtp = False
        self._max_size = None
        self._message_size = None
        self._recipients_truncated = False

    def is_open(self):
        return self._state == STATE_OPEN

    @property
    def is_esmtp(self):
        return self._is_esmtp

    def connection_made(self, transport):
        super(SmtpProtocol, self).connection_made(transport)
        # What should we do if peername is None due to some OS error?
        self._peername = transport.get_extra_info('peername')

    def client_connected(self, reader, writer):
        """The StreamReaderProtocol callback where things can truly begin."""
        self.reader = reader
        self.writer = writer
        self._state = STATE_OPEN
        self.connection_open.set_result(True)

    def connection_lost(self, exc):
        # TODO: Something with exc
        self._state = STATE_CLOSED
        self.connection_closed.set_result(None)
        super(SmtpProtocol, self).connection_lost(exc)

    def expect_data(self):
        self._read_mode = READ_MODE_DATA

    def expect_command(self):
        self._read_mode = READ_MODE_COMMAND

    async def run(self):
        # First, wait until we have readers and writers in place
        await self.connection_open

        # Say hi, then process commands.
        await self.send(b'220 ' + self._fqdn + b' ESMTP')
        while not self.connection_closed.done():
            try:
                if self._read_mode == READ_MODE_COMMAND:
                    await self.read_command()
                else:
                    await self.read_data()
            except asyncio.CancelledError:
                break

        await self.close()

    async def read_command(self):
        try:
            line = await self.reader.read_crlf_line()
        except errors.TooMuchDataError:
            await self.send(b'500 Line too long')
            return

        await self.handle_command(line)

    async def read_data(self):
        try:
            data = await self.reader.read_data(max_len=self._max_size)
        except errors.TooMuchDataError:
            await self.send(b'552 Message exceeds fixed maximum size')
            return

        await self.handle_data(data)

    async def handle_data(self, data):
        if self._max_size and len(data) >= self._max_size:
            await self.send(b'522 Message exceeds fixed maximum size')
            return

        asyncio.ensure_future(
            self.handler.message_received(
                self._sender, self._recipients, data), loop=self._loop)

        if self._recipients_truncated:
            response = b'250 Some recipients ok'
        else:
            response = b'250 Ok'

        self.reset_state()
        await self.send(response)

    async def handle_command(self, line):
        ix = line.find(b' ')
        if ix > 0:
            cmd = line[:ix]
            arg = line[ix:].strip()
        else:
            cmd = line.strip()
            arg = None

        method = self.COMMANDS[cmd.upper()]
        if method:
            await method(self, arg)
        else:
            await self.send(b'500 PEBKAC')

    async def close(self):
        try:
            if self.writer.can_write_eof():
                await self.writer.write_eof()
        except Exception:
            pass

        try:
            await self.writer.close()
        except Exception:
            pass

        self.connection_closed.set_result(None)

    async def send(self, line):
        if not line.endswith(const.LINE_TERM):
            line += const.LINE_TERM

        self.writer.write(line)

        await self.writer.drain()

    #
    # SMTP commands follow.
    #

    async def helo(self, arg):
        if self._helo:
            await self.send(b'503 Duplicate HELO/EHLO')
        elif not arg:
            await self.send(b'501 Syntax: HELO hostname')
        else:
            self._is_esmtp = False
            self._helo = arg
            await self.send(b'250 ' + self._fqdn)

    async def ehlo(self, arg):
        if self._helo:
            await self.send(b'503 Duplicate HELO/EHLO')
            return

        helo, arg = self.split_command(arg)

        if not helo:
            await self.send(b'501 Syntax: EHLO hostname')
            return

        if arg:
            # TODO: Implement arg handling.  Is that even legal SMTP?
            pass

        self._is_esmtp = True
        self._helo = helo
        resp_lines = [b'250-' + self._fqdn]
        if self._max_size:
            sz = bytes(str(self._max_size), 'ascii')
            resp_lines.append(b'250-SIZE ' + sz)
        resp_lines.append(b'250 HELP')
        resp = const.LINE_TERM.join(resp_lines)
        await self.send(resp)

    async def vrfy(self, arg):
        if arg is None:
            self.writer.write(b'501 Syntax: VRFY <')
            return

        if arg in self._allowed_recipients:
            await self.send(
                b'252 Cannot verify user, but will accept message and '
                b'attempt delivery')
            return

        result = await self.handler.verify(arg)
        if result:
            self._allowed_recipients.add(result)
            self.send(b'252 Cannot verify user, but will accept message and '
                      b' attempt delivery')
        else:
            self.send(b'502 Could not verify ' + arg)

    async def rset(self, arg):
        self.reset_state()
        await self.send(b'250 Ok')

    async def mail(self, arg):
        if not arg:
            await self.send(b'501 Syntax: MAIL FROM:<address>')
            return
        if not self._helo:
            await self.send(b'503 Error: Send HELO first')
            return
        if self._sender:
            await self.send(b'503 Error: Nested MAIL command')
            return

        arg = self.strip_keyword(arg, b'FROM:')
        addr, params = self.parse_addr(arg)

        if not addr:
            await self.send(b'501 Syntax: MAIL FROM: <address>')
            return

        if not self.is_esmtp and params:
            await self.send(b'501 Syntax: MAIL FROM: <address>')
            return

        if params:
            params = self.parse_mail_params(params)

            if not params:
                await self.send(b'501 Syntax: MAIL FROM: <address>')
                return

            for k, v in dict(params).items():
                if k.upper() == b'SIZE':
                    try:
                        n = int(str(v, 'ascii'))
                        if self._max_size and n >= self._max_size:
                            m = (b'552 Message size exceeds fixed maximium '
                                 b'message size')
                            await self.send(m)
                            return

                        self._message_size = n
                        del params[k]
                    except Exception:
                        break

            if params:
                await self.send(b'555 Unrecognized extension')
                return

        self._sender = addr
        await self.send(b'250 Ok')

    async def rcpt(self, arg):
        if not self._helo:
            await self.send(b'503 Error: Send HELO first')
            return

        if not self._sender:
            await self.send(b'503 Error: Send MAIL first')
            return

        if not arg:
            await self.send(b'501 Syntax: RCPT <address>')
            return

        arg = self.strip_keyword(arg, b'TO:')
        addr, params = self.parse_addr(arg)

        if not addr:
            await self.send(b'501 Syntax: RCPT <address>')
            return

        if params:
            await self.send(b'555 Unrecognized extension')
            return

        if self._message_size and self._max_size:
            nrecips = len(self._recipients) + 1
            total_size = self._max_size * nrecips

            if self._max_size <= total_size:
                self._recipients_truncated = True
                msg = b'552 Channel size limit exceeded: ' + addr
                await self.send(msg)
                return

        self._recipients.append(addr)
        await self.send(b'250 Ok')

    async def data(self, arg):
        if arg:
            await self.send(b'501 Syntax: Data')
            return

        if not self._helo:
            await self.send(b'503 Error: Send HELO first')
            return

        if not self._sender:
            await self.send(b'503 Error: Send MAIL first')
            return

        if not self._recipients:
            await self.send(b'503 Error: Need RCPT command')
            return

        self.expect_data()
        await self.send(b'354 End data with <CRLF>.<CRLF>')

    async def noop(self, arg):
        if arg:
            await self.send(b'501 Syntax: NOOP')
        else:
            await self.send(b'250 Ok')

    async def quit(self, arg):
        if arg:
            await self.send(b'501 Syntax: QUIT')
            return

        await self.send(b'221 Ok')
        await self.close()

    async def expn(self, arg):
        await self.send(b'502 Unimplemented')

    def strip_keyword(self, line, keyword):
        if line.upper().startswith(keyword.upper()):
            sz = len(keyword)
            return line[sz:].strip()
        return b''

    def parse_addr(self, maybe_addr):
        if not maybe_addr:
            return b'', b''
        if maybe_addr.lstrip().startswith(b'<'):
            address, rest = get_angle_addr(str(maybe_addr, 'ascii'))
        else:
            address, rest = get_addr_spec(str(maybe_addr, 'ascii'))

        if not address:
            return None, rest

        return address.addr_spec.encode('ascii'), rest.encode('ascii')

    def split_command(self, line):
        arg = line.strip()
        ix = arg.find(b' ')
        if ix >= 0:
            return arg[:ix], arg[ix + 1].strip()
        else:
            return arg, None

    def parse_mail_params(self, param_line):
        pairs = param_line.split(b' ')
        params = {}
        for pair in pairs:
            ix = pair.find(b'=')
            if ix < 0:
                return None
            key = pair[:ix]
            value = pair[ix+1:]
            params[key] = value
        return params
