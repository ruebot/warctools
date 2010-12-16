"""An object to represent warc records, using the abstract record in record.py"""

import re

from warctools.record import ArchiveRecord,ArchiveParser
from warctools.archive_detect import register_record_type

bad_lines = 5 # when to give up looking for the version stamp

@ArchiveRecord.HEADERS(
    DATE='WARC-Date',
    TYPE = 'WARC-Type',
    ID = 'WARC-Record-ID',
    CONTENT_LENGTH = 'Content-Length',
    CONTENT_TYPE = 'Content-Type',
    URL='WARC-Target-URI',
)
class WarcRecord(ArchiveRecord):
    def __init__(self, version=None, headers=None, content=None, errors=None):
        ArchiveRecord.__init__(self,headers,content,errors) 
        self.version = version

    @property
    def id(self):
        return self.get_header(self.ID)

    def _write_to(self, out, nl):  
        """WARC Format:
            VERSION NL
            (Key: Value NL)* 
            NL
            CONTENT NL
            NL
            
            don't write multi line headers
        """
        out.write(self.version)
        out.write(nl)
        for k,v in self.headers:
            out.write(k)
            out.write(": ")
            out.write(v)
            out.write(nl)
        content_type, buffer = self.content
        if content_type:
            out.write(self.CONTENT_TYPE)
            out.write(": ")
            out.write(content_type)
            out.write(nl)
        if buffer:
            content_length = str(len(buffer))
            out.write(self.CONTENT_LENGTH)
            out.write(": ")
            out.write(content_length)
            out.write(nl)

        # end of header blank nl
        out.write(nl)
        if buffer:
            out.write(buffer)
        out.write(nl)
        out.write(nl)

    def repair(self):
        pass

    def validate(self):
        return self.errors

    @classmethod
    def make_parser(self):
        return WarcParser()

def rx(pat):
    return re.compile(pat,flags=re.IGNORECASE)

version_rx = rx(r'^(?P<prefix>.*?)(?P<version>\s*WARC/(?P<number>.*?))' '(?P<nl>\r\n|\r|\n)\\Z')
# a header is key: <ws> value plus any following lines with leading whitespace
header_rx = rx(r'^(?P<name>.*?):\s?(?P<value>.*?)' '(?P<nl>\r\n|\r|\n)\\Z')
value_rx = rx(r'^\s+(?P<value>.+?)' '(?P<nl>\r\n|\r|\n)\\Z')
nl_rx=rx('^(?P<nl>\r\n|\r|\n\\Z)')
length_rx = rx('^'+WarcRecord.CONTENT_LENGTH+'$')
type_rx = rx('^'+WarcRecord.CONTENT_TYPE+'$')

required_headers = set((
    WarcRecord.TYPE.lower(), 
    WarcRecord.ID.lower(), 
    WarcRecord.CONTENT_LENGTH.lower(), 
    WarcRecord.DATE.lower(),
))

class WarcParser(ArchiveParser):
    def __init__(self):
        self.trailing_newlines = 0

    def parse(self,stream):
        """Reads a warc record from the stream, returns a tuple (record, errors). 
        Either records is null or errors is null. Any record-specific errors are 
        contained in the record - errors is only used when *nothing* could be parsed"""
        errors = []
        version = None
        # find WARC/.*
        line = stream.readline()
        newlines = self.trailing_newlines
        if newlines > 0:
            while line:
                match = nl_rx.match(line)
                if match and newlines > 0:
                    newlines-=1
                    if match.group('nl') != '\x0d\x0a':
                        errors.append(('incorrect trailing newline', match.group('nl')))
                    line = stream.readline()
                    if newlines == 0:
                        break
                else:
                    break
                    
            if newlines > 0:
                errors+=('less than two terminating newlines at end of previous record, missing', newlines)

        while line:
            match = version_rx.match(line)

            if match or not line:
                version = match.group('version')
                break
            elif not nl_rx.match(line):
                errors.append(('ignored line', line)) 
                if len(errors) > bad_lines:
                    errors.append(('too many errors, giving up hope',))
                    return (None,errors)  
            line = stream.readline()
        if not line:
            if version:
                errors.append('warc version but no headers', version)
            self.trailing_newlines = 0
            return (None, errors)
        if line:
            content_length = 0
            content_type = None

            record = WarcRecord(errors=errors, version=version)


            if match.group('nl') != '\x0d\x0a':
                record.error('incorrect newline in version', match.group('nl'))

            if match.group('number') != '1.0':
                record.error('version field is not "1.0"', match.group('number'))


            prefix = match.group('prefix')

            if prefix:
                record.error('bad prefix on WARC version header', prefix)
            
            #Read headers
            line = stream.readline()
            while line and not nl_rx.match(line):       
              
                #print 'header', repr(line)
                match = header_rx.match(line)
                if match:
                    if match.group('nl') != '\x0d\x0a':
                        record.error('incorrect newline in header', match.group('nl'))
                    name = match.group('name').strip()
                    value = [match.group('value').strip()]
                    #print 'match',name, value

                    line = stream.readline()
                    match = value_rx.match(line)
                    while match:
                        #print 'follow', repr(line)
                        if match.group('nl') != '\x0d\x0a':
                            record.error('incorrect newline in follow header',line, match.group('nl'))
                        value.append(match.group('value').strip())
                        line = stream.readline()
                        match = value_rx.match(line)

                    value = " ".join(value)
                    
                    if type_rx.match(name):
                        if value:
                            content_type = value
                        else:
                            record.error('invalid header',name,value) 
                    elif length_rx.match(name):
                        try:
                            #print name, value
                            content_length = int(value)
                            #print content_length
                        except ValueError:
                            record.error('invalid header',name,value) 
                    else:
                        record.headers.append((name,value))

            # have read blank line following headers
            
            # read content
            if content_length is not None:
                if content_length > 0:
                    content=[]
                    length = 0
                    while length < content_length:
                        line = stream.readline()
                        if not line:
                               # print 'no more data' 
                                break
                        content.append(line)
                        length+=len(line)
                        #print length, content_length, line
                    #else:
                        # print 'last line of content', repr(line)
                    content="".join(content)
                    content, line = content[0:content_length], content[content_length:]
                    if len(content)!= content_length:
                        record.error('content length mismatch (is, claims)', len(content), content_length)
                    record.content = (content_type, content)
            else:   
                record.error('missing header', WarcRecord.CONTENT_LENGTH)

            #print 'read content', repr(line)
            # have read trailing newlines

            # check mandatory headers
            #   WARC-Type
            #   WARC-Date WARC-Record-ID Content-Length
            
            # ignore mandatory newlines for now
            # because they are missing.
            # instead we trim a number of them off the next
            # parse

            # we can't re-wind easily without wrapping
            # every file handle

            # not brilliant but hey-ho



            self.trailing_newlines = 2

            return (record, ())

    def trim(self, stream):
        """read the end of the file"""
        newlines = self.trailing_newlines
        self.trailing_newlines = 0
        errors = []
        if newlines:
            line = stream.readline()
            while line:
                #print 'trimming', repr(line)
                match = nl_rx.match(line)
                if match:
                    if match.group('nl') != '\x0d\x0a':
                        errors.append(('incorrect trailing newline', match.group('nl')))
                    newlines-=1
                    #print 'newline'
                    if newlines == 0:
                        break

                else:
                    #print 'line', line, newlines
                    newlines = 0
                    errors.append(('trailing data after content', line))
                line = stream.readline()
            if newlines > 0:
                errors+=('less than two terminating newlines at end of record, missing', newlines)

        return errors

                    
            
register_record_type(version_rx, WarcRecord)