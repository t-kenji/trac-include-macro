# -*- coding: utf-8 -*-
#
# Copyright (C) 2007-2008 Noah Kantrowitz <noah@coderanger.net>
# Copyright (C) 2012 Ryan J Ollos <ryan.j.ollos@gmail.com>
# Copyright (C) 2014 Steffen Hoffmann <hoff.st@web.de>
# Copyright (C) 2015 Andre Auzi <aauzi@free.fr>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#----------------------------------------------------------------------

import re
import sys
import urllib2
import mimetools
from StringIO import StringIO

from genshi.core import escape
from genshi.filters.html import HTMLSanitizer
from genshi.input import HTMLParser, ParseError
from trac.core import Component, TracError, implements
from trac.mimeview.api import Mimeview, get_mimetype, Context
from trac.perm import IPermissionRequestor
from trac.resource import ResourceNotFound
from trac.ticket.model import Ticket
from trac.versioncontrol.api import RepositoryManager
from trac.wiki.api import WikiSystem, IWikiPageManipulator, parse_args
from trac.wiki.formatter import system_message
from trac.wiki.macros import WikiMacroBase
from trac.wiki.model import WikiPage
from trac.web.chrome import web_context

from pystache.common import MissingTags, TemplateNotFoundError
from pystache.context import KeyNotFoundError
from pystache.renderengine import context_get
from pystache.renderer import Renderer

#------------------------------------------------------------

class pystacheRenderer(Renderer):
    """
A class for rendering mustache templates.
This class supports several rendering options which are described in
the constructor's docstring. Other behavior can be customized by
subclassing this class.
For example, one can pass a string-string dictionary to the constructor
to bypass loading partials from the file system:
>>> partials = {'partial': 'Hello, {{thing}}!'}
>>> renderer = Renderer(partials=partials)
>>> # We apply print to make the test work in Python 3 after 2to3.
>>> print renderer.render('{{>partial}}', {'thing': 'world'})
Hello, world!
To customize string coercion (e.g. to render False values as ''), one can
subclass this class. For example:
class MyRenderer(Renderer):
def str_coerce(self, val):
if not val:
return ''
else:
return str(val)
    """
    @staticmethod
    def _escape(u):
        return u

    def __init__(self):
        Renderer.__init__(self,
                          file_extension=False,
                          escape=pystacheRenderer._escape,
                          missing_tags=MissingTags.ignore)

    def _make_resolve_partial(self):
        """
        Return the resolve_partial function to pass to RenderEngine.__init__().

        """
        load_partial = self._make_load_partial()

        if self._is_missing_tags_strict(): # pragma: no cover
            return load_partial            # pragma no cover

        # Otherwise, ignore missing tags.
        def resolve_partial(name):
            #TODO: implement include proxy?
            try:
                return load_partial(name)
            except TemplateNotFoundError:
                return u'`{?>%s?}`'%(name)

        return resolve_partial

    def _make_resolve_context(self):
        """
        Return the resolve_context function to pass to RenderEngine.__init__().
        """
        if self._is_missing_tags_strict():  # pragma: no cover
            return context_get              # pragma: no cover

        # Otherwise, ignore missing tags.
        def resolve_context(stack, name):
            try:
                return context_get(stack, name)
            except KeyNotFoundError:
                return u'`{?%s?}`'%(name)

        return resolve_context

#------------------------------------------------------------

class IncludeProcessorException(Exception):
    """Base class for include processor exception
    """

class SystemMessageException(IncludeProcessorException):
    """Exception to produce a system_message
    """
    def __init__(self, msg, text=None):
        IncludeProcessorException.__init__(self, msg)
        self.text = text

class PermissionException(IncludeProcessorException):
    """Exception raised in case of permission issue
    """

class NotWikiTextException(IncludeProcessorException):
    """Exception raised in case of an attempt of processing
something different from Wiki text when not expected
    """

#----------------------------------------------------------------------
class SelfProxy(object):
    def __init__(self, url, line):
        self.url = url
        self.lineno = unicode(line)

#----------------------------------------------------------------------
# Include modules.
#----------------------------------------------------------------------
class IncludeProcessor(object):
    """Does the include processing operations
    """

    # Include macro detection re
    trac_include_re = re.compile(r'[^[]?(?P<block>\[\[(?P<name>((Include)|(pystache)))\((?P<content>.*?)\)\]\])[^]]?')
    # Execute macro detection re
    trac_start_execute_re = re.compile(r'[^{]?\{\{\{\s*\n?\s*#!(?P<name>((Include)|(pystache)))', re.MULTILINE)

    # Evaluate macro detection re
    trac_evaluate_re = re.compile(r'[^{]?(?P<block>\{\{(?P<eval>[^{]?.*?[^}]?)\}\})[^}]?''')

    open_block_re =re.compile(r'[^{]?(?P<block>\{{3})[^{]?')
    close_block_re =re.compile(r'[^}]?(?P<block>\}{3})[^}]?')
    quote_monospace_block_re = re.compile(r'[^`]?(?P<block>`[^`]?.*?[^`]?`)[^`]?')
    curly_braces_monospace_block_re = re.compile(r'[^{]?(?P<block>\{{3}[^{]?[^\n]*?[^}]?\}{3})[^}]?')
    shebang_re = re.compile(r'\s*\n?\s*#!(?P<name>\w+)(?P<args>.*?)\r?\n\r?(?P<exec>.*)',
                            re.MULTILINE|re.DOTALL)

    # MIME type of format the processor works with
    wiki_text_format = 'text/x-trac-wiki'

    # Default output formats for sources that need them
    default_formats = {
        'wiki': 'text/x-trac-wiki',
    }

    @staticmethod
    def validate_wiki_page(perm, text):
        errors = []
        if (IncludeProcessor.trac_include_re.search(text) and
            not perm.has_permission('INCLUDE_CREATE')):
            errors.append((None, 'INCLUDE_CREATE denied.'))
        if ((IncludeProcessor.trac_start_execute_re.search(text) or
             IncludeProcessor.trac_evaluate_re.search(text)) and
            not perm.has_permission('TEMPLATE_CREATE')):
            errors.append((None, 'TEMPLATE_CREATE denied.'))
        return errors

    def prepare_wiki_page(self, id, text):
        if id is None:
            raise SystemMessageException('resource id is None')

        if text is None:
            text = ''

        caller_url = 'wiki://' + id
        self.wiki_text_only = True
        self.output_buffer = StringIO()
        try:
            self._prepare_text(caller_url, 1, text, False)
            text = self.output_buffer.getvalue()
        finally:
            self.output_buffer.close()
            self.output_buffer = None

        return text

    def __init__(self, env, req):
        self.log = env.log
        self.env = env
        self.req = req
        self.perm = req.perm
        self.pystache_renderer = pystacheRenderer()
        self.urls_stack = []
        self.globals = dict()
        self.locals = dict()
        self.globals['env'] = self.env
        self.globals['req'] = self.req
        self.pystache_context = None
        self.pystache_input_buffer = None
        self.output_buffer = None

    def _redirect_to_pystache(self, url, line, to_pystache):
        if to_pystache:
            self._setup_pystache_context()
            if self.pystache_input_buffer is None:
                self.pystache_input_buffer = StringIO()
                self.pystache_context['self'] = SelfProxy(url, line)
        else:
            if self.pystache_input_buffer is not None:
                text = self.pystache_renderer.render(
                    self.pystache_input_buffer.getvalue(),
                    self.pystache_context)
                self.output_buffer.write(text)
                self.pystache_input_buffer.close()
                self.pystache_input_buffer = None

    def _setup_pystache_context(self):
        if self.pystache_context is not None:
            return

        def expand_value(d, n, v):
            if isinstance(v, list):
                l = []
                for nn in range(0,len(v)):
                    nnn = '%s[%d]'%(n,nn)
                    nv = v[nn]
                    d[nnn] = nv
                    l.append({u'name':nnn, u'index':nn, u'value':nv})
                d[n]=l
            elif isinstance(v, dict):
                l = []
                for nn, nv in v.items():
                    nnn = '%s[%s]'%(n,nn)
                    d[nnn] = nv
                    l.append({u'name':nnn, u'index':nn, u'value':nv})
                d[n]=l
            else:
                d[n] = v

        scope = []
        for (u, globals, l) in self.urls_stack:
            scope.append(globals)
        scope += [self.globals, self.locals]

        args = dict()
        for d in scope:
            for n, v in d.items():
                expand_value(args, n, v)

        self.pystache_context = args

    def _write(self, text):
         if self.pystache_input_buffer is not None:
               self.pystache_input_buffer.write(text)
         else:
               self.output_buffer.write(text)

    def _prepare_text(self, url, line, text, starting_with_pystache, preformatted_block=False):
        def find_first_block(re_list, text, pos):
            found_m = None
            start = len(text)
            for r in re_list:
                m = r.search(text, pos)
                if (m and
                    len(m.group('block')) and
                    (m.start('block') < start)):
                    found_m = m
                    start = m.start('block')
            return found_m

        def find_close_block(text, pos):
            re_list = [self.quote_monospace_block_re,
                       self.curly_braces_monospace_block_re,
                       self.open_block_re,
                       self.close_block_re]
            count = 1
            while True:
                m = find_first_block(re_list, text, pos)
                if m is None:
                    break
                if m.re is self.open_block_re:
                    count += 1
                elif m.re is self.close_block_re:
                    count -= 1
                    if count == 0:
                        return m

                # skip the found block
                # will notably skip the monospace
                # forms {{{...}}} and `...`
                pos = m.end('block')
            return None

        re_list = [self.quote_monospace_block_re,
                   #self.curly_braces_monospace_block_re,
                   self.trac_include_re,
                   self.open_block_re]

        saved_output_buffer = None

        self._redirect_to_pystache(url, line, starting_with_pystache)

        prev = 0
        while True:
            m = find_first_block(re_list, text, prev)
            if m is None:
                # block start not found => break here
                break

            # skip and copy until start
            self._write( text[prev:m.start('block')] )
            line += text.count('\n', prev, m.start('block'))
            prev = m.start('block')

            if m.re is self.open_block_re:
                # process a block start
                stop_m = find_close_block(text, m.end('block'))
                if stop_m is None:
                    # block end not found => break here
                    break

                # flush the treatment in current context
                self._redirect_to_pystache(url, line, False)

                if text[m.end('block'):stop_m.start('block')].count('\n') == 0:
                    # monospace block
                    # flush the treatment in current context
                    # copy the whole block
                    self._write( text[m.start('block'):stop_m.end('block')] )
                    # skip the block
                    line += text.count('\n', m.start('block'), stop_m.end('block'))
                    self._redirect_to_pystache(url, line, starting_with_pystache)
                    prev = stop_m.end('block')
                    continue

                # see if block is an execute block
                shebang_m = self.shebang_re.match(text,
                                                  m.end('block'),
                                                  stop_m.start('block'))
                if not shebang_m:
                    # preformatted text block

                    # copy the start block mark
                    self._write(m.group('block'))

                    # recurse within the block in preformatted mode
                    self._prepare_text(url, line,
                                        text[m.end('block'):stop_m.start('block')],
                                        starting_with_pystache, True)

                    # copy the end block mark
                    self._redirect_to_pystache(url, line, False)
                    self._write(stop_m.group('block'))

                elif not preformatted_block and shebang_m.group('name') in ['Include', 'pystache']:

                    # Include processor block
                    # execute the block include
                    exec_line = line + text[m.start('block'):shebang_m.start('exec')].count('\n')
                    self._process_execute(url, exec_line, shebang_m)

                else:
                    # other processor block
                    # copy the start block mark
                    self._write(m.group('block'))

                    # recurse within the block
                    self._prepare_text(url, line,
                                        text[m.end('block'):stop_m.start('block')],
                                        starting_with_pystache)

                    # copy the end block mark
                    self._redirect_to_pystache(url, line, False)
                    self._write(stop_m.group('block'))

                # skip the block
                line += text.count('\n', m.start('block'), stop_m.end('block'))
                self._redirect_to_pystache(url, line, starting_with_pystache)
                prev = stop_m.end('block')
                continue
            elif m.re is self.quote_monospace_block_re:
                # monospace block
                # flush the treatment in current context
                # copy the whole block
                self._redirect_to_pystache(url, line, False)
                self._write( m.group('block') )

            elif m.re is self.trac_include_re:
                if not preformatted_block:
                    # Include macro call
                    # process the include

                    self._process_include(url, line, m)
                else:
                    # just do the transformation
                    self._write( m.group('block') )

            # restore processing context
            # skip the remaining of the block
            self._redirect_to_pystache(url, line, starting_with_pystache)
            line += m.group('block').count('\n')
            prev = m.end('block')

        # copy the remainings
        # flush the treatment in current context
        self._write( text[prev:] )
        self._redirect_to_pystache(url, line, False)

    def _process_execute(self, caller_url, line, match):
        text = match.group('args').strip()
        text = self.pystache_renderer.render(text)
        listed_args, named_args = parse_args(text)
        try:
            self._proxy_execute(caller_url, line,
                                listed_args, named_args,
                                match.group('exec'))
            return
        except NotWikiTextException:
            pass
        # in case of non wiki text MIME type
        # enclose the result in a quote block
        self._redirect_to_pystache(caller_url, line, False)
        self._write('{{{\n')
        named_args['mime_type'] = self.wiki_text_format
        self._proxy_execute(caller_url, line,
                            listed_args, named_args,
                            match.group('exec'))
        self._redirect_to_pystache(caller_url, line, False)
        self._write('}}}')

    def _process_include(self, caller_url, line, match):
        text = match.group('content').strip()
        text = self.pystache_renderer.render(text)
        listed_args, named_args = parse_args(text)
        try:
            self._proxy_include(caller_url, line, listed_args, named_args)
        except NotWikiTextException:
            self._redirect_to_pystache(caller_url, line, False)
            self._write(match.group('block'))
        except PermissionException as e:
            if (str(e) == 'INCLUDE_URL'):
                self.log.info('%s:%d Blocking attempt of %s to include URL by %s',
                              caller_url, line, self.req.authname, match.group())
        except SystemMessageException as e:
            self._write( SystemMessageMacro.wiki_text(
                'Include failed',
                '%s:%d:%s'%(caller_url, line, str(e))))

    def _proxy_execute(self, url, line, listed_args, named_args, text):
        # setup context for new arguments
        self.urls_stack.append((url, self.globals, self.locals))
        self.globals = dict()
        self.locals = dict()
        self.pystache_context = None

        mime_type = None
        try:
            ignored_url, mime_type = self._process_arguments(listed_args, named_args, True)

            # use wiki text format by default
            if mime_type is None:
                mime_type = self.wiki_text_format

            # only expand trac wiki text resource
            if self.wiki_text_only and (mime_type != self.wiki_text_format):
                raise NotWikiTextException();

            # do conversion
            self._prepare_text(url, line, text, True)
        finally:
            # restore context after include
            url, self.globals, self.locals = self.urls_stack.pop()
            self.pystache_context = None

        return mime_type

    def _proxy_include(self, caller_url, line, listed_args, named_args):
        # setup context for include
        self.urls_stack.append((caller_url, self.globals, self.locals))
        self.globals = dict()
        self.locals = dict()
        self.pystache_context = None

        try:
            url, mime_type = self._process_arguments(listed_args, named_args, False)
            url, text, mime_type = self._get_include(caller_url, url, mime_type)

            # avoid recursion
            for (prev_url, g, l) in self.urls_stack:
                if (url == prev_url):
                    raise SystemMessageException('Recursion in "%s" detected'%(prev_url))

            # only expand trac wiki text resource
            if self.wiki_text_only and (mime_type != self.wiki_text_format):
                raise NotWikiTextException();

            # do recursion
            self._prepare_text(url, 1, text, True)
        finally:
            # restore context after include
            url, self.globals, self.locals = self.urls_stack.pop()
            self.pystache_context = None

        return mime_type

    def _process_arguments(self, listed_args, named_args, processor_mode):
        url = None

        start = 0
        if named_args.has_key('src'):
            url = named_args['src']
        elif not processor_mode and len(listed_args) > 0:
            url = listed_args[0]
            start = 1

        if not url:
            url = None

        if url and not named_args.has_key('src'):
            named_args['src'] = url

        mime_type = None

        if named_args.has_key('mime_type'):
            mime_type = named_args['mime_type'].strip()
        elif processor_mode:
            for (u,globals,l) in self.urls_stack:
                if globals.has_key('mime_type'):
                    mime_type = globals['mime_type']
            if mime_type is None and self.globals.has_key('mime_type'):
                mime_type = self.globals['mime_type']
        elif len(listed_args) > start:
            mime_type = listed_args[-1].strip()

        if not mime_type:
            mime_type = None

        if mime_type and not named_args.has_key('mime_type'):
            named_args['mime_type'] = mime_type

        for n, v in named_args.items():
            self.globals[n] = v

        self.locals['argv'] = listed_args

        return url, mime_type

    def get_include(self, id, line, content, args):
        if id is None:
            raise SystemMessageException('resource id is None')
        if (args is not None) and (type(args) is not dict):
            raise SystemMessageException('type(args) is not dict')
        if content is None:
            content = ''

        text = ''
        mime_type = None

        caller_url = 'wiki://' + id
        self.wiki_text_only = False
        self.output_buffer = StringIO()
        try:
            if args is not None:
                listed_args, named_args = [], args
                mime_type = self._proxy_execute(caller_url, line,
                                          listed_args, named_args, content)
            else:
                listed_args, named_args = parse_args(content)
                mime_type = self._proxy_include(caller_url, line,
                                          listed_args, named_args)

                text = self.output_buffer.getvalue()
        finally:
            self.output_buffer.close()
            self.output_buffer = None

        return text, mime_type

    def _get_include(self, caller_url, url, mime_type):
        out = None

        if url is None:
            raise SystemMessageException('source parameter is missing')

        source_format, source_obj = None, None
        try:
            source_format, source_obj = url.split(':', 1)
        except ValueError:  # If no : is present, assume its a wiki page
            source_format, source_obj = 'wiki', url
            url = 'wiki://'+ source_obj

        # Apply a default format if needed
        if (mime_type is None) and self.default_formats.has_key(source_format):
            mime_type = self.default_formats[source_format]

        if source_format in ('http', 'https', 'ftp'):
            if (self.wiki_text_only and (mime_type != self.wiki_text_format)):
                return url, '', mime_type

            url, out, mime_type = self._get_url(caller_url, url, mime_type)
        elif source_format == 'wiki':
            url, out = self._get_page(caller_url, source_obj)
        elif source_format in ('source', 'browser', 'repos'):
            out, mime_type = self._get_source(caller_url, source_obj, mime_type)
        elif source_format == 'ticket':
            out, mime_type = self._get_ticket(source_obj, mime_type)
        else:
            raise SystemMessageException('Unsupported realm %s' % source_format)

        if out is None:
            raise SystemMessageException('Could not resolve %s'%(url))

        if (self.wiki_text_only and (mime_type != self.wiki_text_format)):
            return url, '', mime_type

        return url, out, mime_type

    def _get_url(self, caller_url, url, mime_type):
        out = None

        # Since I can't really do recursion checking, and because this
        # could be a source of abuse allow selectively blocking it.
        # RFE: Allow blacklist/whitelist patterns for URLS. <NPK>
        # RFE: Track page edits and prevent unauthorized users from ever entering a URL include. <NPK>
        if not self.perm.has_permission('INCLUDE_URL'):
            raise PermissionException('INCLUDE_URL')
        try:
            urlf = urllib2.urlopen(url)
            info = urlf.info()
            outf = StringIO()
            mimetools.decode(urlf, outf, info.getencoding())
            out = unicode(outf.read())

            # read back the actual url to properly handle
            # potential recursion in case or redirections
            url = urlf.geturl()

            # retrieves the MIME type from the header
            if mime_type is None:
                mime_type = info.gettype()
        except urllib2.URLError as e:
            raise SystemMessageException('Error while retrieving file: "%s"'%(str(e)))
        except TracError as e:                                                    # pragma: no cover
            raise SystemMessageException('Error while previewing: "%s"'%(str(e))) # pragma: no cover

        return url, out, mime_type

    def _get_page(self, caller_url, source_obj):
        out = None

        page_name, page_version = _split_path(source_obj)

        # Relative link resolution adapted from Trac 1.1.2dev.
        # Hint: Only attempt this in wiki rendering context.


        referrer = caller_url
        if referrer.startswith('wiki://'):
            referrer = referrer.replace('wiki://', '', 1)
        ws = WikiSystem(self.env)
        if page_name.startswith('/'):
            page_name = page_name.lstrip('/')
        elif page_name.startswith(('./', '../')) or \
            page_name in ('.', '..'):
            page_name = _resolve_relative_name(ws, page_name, referrer)
        else:
            page_name = _resolve_scoped_name(ws, page_name, referrer)

        page = WikiPage(self.env, page_name, page_version)
        if not 'WIKI_VIEW' in self.perm(page.resource):
            raise PermissionException('WIKI_VIEW')

        if not page.exists:
            if page_version:
                raise SystemMessageException('No version "%s" for wiki page "%s"' % (
                    page_version, page_name))

            raise SystemMessageException('Wiki page "%s" does not exist' % (page_name))

        out = page.text

        url = 'wiki://'
        if page_version:
            url += '@'.join([page_name, page_version])
        else:
            url += page_name

        return url, out

    def _get_source(self, caller_url, source_obj, mime_type):
        out = None

        if not self.perm.has_permission('FILE_VIEW'):
            raise PermissionException('FILE_VIEW')

        repos_mgr = RepositoryManager(self.env)
        try:  # 0.12+
            repos_name, repos, source_obj = \
                    repos_mgr.get_repository_by_path(source_obj)
        except AttributeError:  # 0.11                          # pragma: no cover
            repos = repos_mgr.get_repository(self.req.authname) # pragma: no cover

        if repos is None:
            raise SystemMessageException('Repository for "%s" is not accessible'%(source_obj))

        path, rev = _split_path(source_obj)
        node = repos.get_node(path, rev)
        out = node.get_content().read()

        if mime_type is None:
            mime_type = node.content_type or get_mimetype(path, out)

        return out, mime_type

    def _get_ticket(self, source_obj, mime_type):
        out = None

        try:
            ticket_num, source_obj = source_obj.split(':', 1)
        except ValueError:  # If no : is present, assume its a wiki page
            raise SystemMessageException('Ticket field must be specified')

        try:
            if not Ticket.id_is_valid(ticket_num):
                raise SystemMessageException('"%s" is not a valid ticket id' % ticket_num)
        except ValueError:
            raise SystemMessageException('"%s" is not a valid ticket id' % ticket_num)

        try:
            ticket = Ticket(self.env, ticket_num)
        except ResourceNotFound:
            raise SystemMessageException('Ticket "%s" does not exist' % ticket_num)

        if not 'TICKET_VIEW' in self.perm(ticket.resource):
            raise PermissionException('TICKET_VIEW')

        try:
            source_format, comment_num = source_obj.split(':', 1)
        except ValueError:
            raise SystemMessageException('Malformed ticket field "%s"' % source_obj)

        if source_format != 'comment':
            raise SystemMessageException('Unsupported ticket field "%s"' % source_format)

        changelog = ticket.get_changelog()
        if not changelog:
            return None, mime_type

        out = None
        for (ts, author, field, oldval, newval, permanent) in changelog:
            if field == 'comment' and oldval == comment_num:
                mime_type = 'text/x-trac-wiki'
                out = newval
            break

        if not out:
            raise SystemMessageException("Comment %s does not exist for Ticket %s" % (comment_num, ticket_num))

        return out, mime_type

#----------------------------------------------------------------------
# System message macro.
#----------------------------------------------------------------------

class SystemMessageMacro(WikiMacroBase):
    """A macro to include system messages in wiki pages.
    """

    # IWikiMacroProvider methods
    def expand_macro(self, formatter, name, content, args=None):
        listed_args, named_args = parse_args(content)

        msg = ''
        if len(listed_args) > 0:
            msg = listed_args[0]

        text = None
        if len(listed_args) > 1:
            text = ', '.join(listed_args[1:])

        return system_message(msg, text)

    @staticmethod
    def wiki_text(msg, text=None):
        if text:
            return '[[SystemMessage(%s, %s)]]'%(msg, text)
        return '[[SystemMessage(%s)]]'%(msg)

#----------------------------------------------------------------------
# Include macro.
#----------------------------------------------------------------------

class IncludeMacro(WikiMacroBase):
    """=== Include basics

Include is a macro to include other resources in wiki pages.

Currently supported sources, identified by the source scheme, are:

      * HTTP - http: and https:
      * FTP - ftp:
      * Wiki pages - wiki:
      * Repository files - source:  browser: and repos:
      * Ticket comments - ticket:N:comment:M

The default source is 'wiki'.

An optional second argument sets the output MIME type.
If omitted, it is guessed from the source, notably the
'svn:mime-type' for repository files.

Those two parameters are either named or arguments:
      a. the source is the named parameter `src` or the first
         argument
      b. the MIME is the named parameter `mime_type` or the last
         argument, if it is not used by source

==== Example

Include another wiki page:
       {{{
       [[Include(PageName)]] or [[Include(src="PageName")]]
       }}}

Include the HEAD revision of a reStructuredText file from the repository:
       {{{
       [[Include(source:trunk/docs/README, text/x-rst)]]                   # or
       [[Include(src="source:trunk/docs/README", text/x-rst)]]             # or
       [[Include(source:trunk/docs/README, mime_type="text/x-rst")]]       # or
       [[Include(src="source:trunk/docs/README", mime_type="text/x-rst")]]
       }}}

Include a specific revision of a file from the repository:
       {{{
       [[Include(source:trunk/docs/README@5, text/x-rst)]]                 # and so on...
       }}}

=== Include parameters

In trac wiki text included files, identified by the MIME type `'text/x-trac-wiki'`,
Include replaces the macros named parameters with the syntax `{{name}}`

Similarly, it replaces arguments with the syntax `{{argv[i]}}` where `i` is the
positional index of the argument.

The scope of those parameters is limited to the included source but named
parameters are globals within the inclusion stack whereas arguments are not.

==== Example

    * let be A invoking: `[[Include(src="B", distance="50"]]`
    * having B invoking: `[[Include(src="C",ray="3"]]`
      - in source A, obviously, nor `{{distance}}` neither `{{ray}}` are replaced.
      - in source B, `{{distance}}` is replaced by `50`, `{{ray}}` is still unknown.
      - in source C, finally, both `{{distance}}` and `{{ray}}` are replaced.

Moreover, in the same example:
      - in source B, `{{src}}` is replaced by `B`
      - in source C, `{{src}}` is replaced by `C`

Finally, when arguments are used we have:

    * with A invoking: `[[Include(B, test/x-trac-wiki]]`
    * having B invoking: `[[Include(C)]]`
      - in source B, `{{argv[0]}}` is replaced by `B` and `{{argv[1]}}` by
        `test/x-trac-wiki`
      - in source C, `{{argv[0]}}` is replaced by `C` but `{{argv[1]}}` is not
        available.

=== Include processing

Include processing, like the parameters replacements described above is actually achieved with
''pystache''.

[[http://defunkt.github.com/pystache|Pystache]] is a Python implementation of
[[http://mustache.github.com/|Mustache]].
Mustache is a framework-agnostic, logic-free templating system inspired by
[[http://code.google.com/p/google-ctemplate/|ctemplate]] and
[[http://www.ivan.fomichev.name/2008/05/erlang-template-engine-prototype.html|et]]. Like ctemplate, Mustache "emphasizes separating logic from presentation: it is impossible to embed application logic in this template language."

So far, Include provides the inclusion mechanism therefore the load partials, tagged with `{>name}`, is not implemented as a redirection into the include processing.

This may become an evolution in the future.

For reading convenience, not found keys are kept into the produced Wiki text, enclosed in backquotes and ''interrogation'' marks like this: `{?name?}`

Similarly, not found partials are reported like this: `{?>name?}`

For the fun of it, the listed parameters, beside their presentation as `argv[i]` are also presented
in a plain list named `argv` that lets us use the ''Sections render blocks'' on it with the key `{{value}}`.

  Tip::
    in that case it is probably better to use the named parameters ''src'' and ''mime-type'' to avoid the mixing of values and include parameters.

Please refer to [[https://mustache.github.io/mustache.5.html|man munstache(5)]] for more information on pystache capacities.

==== The environment context : the variable self

In the local environement, a variable named `self` is instanciated.

This variable is a proxy to the Include processor and exposes two attributes:
    * `url`: which is the name of the currently processed wiki file
    * and `lineno`: which is the line number where pystache conversion started in the current file

Similarly, in the global environment, two variables are provided:
    * `env` : to let produce whatever useful information the environment holds
    * and `req` : to identify the source of the current processing if needed

=== Permissions

The three remote sources (http, https and ftp) require INCLUDE_URL permission to be
rendered.

Text creation with Include require INCLUDE_CREATE permission.

Text creation with statements require TEMPLATE_CREATE permission.

=== Configuration

If [[http://trac.edgewall.org/wiki/TracIni#wiki-section|[wiki] render_unsafe_content]]
is off (the default), any produced HTML will be sanitized.

    {{{#!ini
    [wiki]
    render_unsafe_content = false
    }}}

    {{{#!div style="background: #ffd; border: 1px solid"
    ** Caution! **

      This is a potential security risk! Please review the implications of
      [[http://trac.edgewall.org/wiki/TracIni#wiki-section|render_unsafe_content]]
      before using this feature.

    }}}

To enable the plugin:
    {{{#!ini
    [components]
    includemacro.* = enabled
    }}}

=== Nota Bene

Since this is as much a pystache macro as an include macro, the same processing can be
achieved with the macro `[[pystache(...)]]`
"""

    implements(IPermissionRequestor)

    # IPermissionRequestor methods
    def get_permission_actions(self):
        yield 'INCLUDE_URL'
        yield 'INCLUDE_CREATE'
        yield 'TEMPLATE_CREATE'

    implements(IWikiPageManipulator)
    # IWikiPageManipulator methods
    def prepare_wiki_page(self, req, page, fields):
        """Preprocess a wiki page before rendering it.

        :param page: is the `WikiPage` being viewed.

        :param fields: is a dictionary which contains the wiki `text`
          of the page, initially identical to `page.text` but it can
          eventually be transformed in place before being used as
          input to the formatter.
        """

        try:
            processor = IncludeProcessor(self.env, req)
            fields['text'] = processor.prepare_wiki_page(page.resource.id, fields['text'])
        except SystemMessageException as e:
            fields['text'] = SystemMessageMacro.wiki_text(str(e), e.text)
        except IncludeProcessorException as e:                    # pragma: no cover: this is just safety nest
            fields['text'] = SystemMessageMacro.wiki_text(str(e)) # pragma: no cover

    def validate_wiki_page(self, req, page):
        """Validate a wiki page after it's been populated from user input.

        :param page: is the `WikiPage` being edited.

        :return: a list of `(field, message)` tuples, one for each
        problem detected. `field` can be `None` to indicate an
        overall problem with the page. Therefore, a return value of
        `[]` means everything is OK.
        """
        return IncludeProcessor.validate_wiki_page(req.perm, page.text)

    # IWikiMacroProvider methods
    def expand_macro(self, formatter, name, content, args=None):
        """Called by the formatter when rendering the parsed wiki text.

        .. versionadded:: 0.11
        .. versionchanged:: 0.12
           added the `args` parameter

        :param formatter: the wiki `Formatter` currently processing
           the wiki markup

        :param name: is the name by which the macro has been called;
           remember that via `get_macros`, multiple names could be
           associated to this macros. Note that the macro names are
           case sensitive.

        :param content: is the content of the macro call. When called
           using macro syntax (`[[Macro(content)]]`), this is the
           string contained between parentheses, usually containing
           macro arguments. When called using wiki processor syntax
           (`{{{!#Macro ...}}}`), it is the content of the processor
           block, that is, the text starting on the line following the
           macro name.

        :param args: will be a dictionary containing the named
          parameters passed when using the Wiki processor syntax.

        The named parameters can be specified when calling the macro
        using the wiki processor syntax::

        {{{#!Macro arg1=value1 arg2="value 2"`
        ... some content ...
        }}}

        In this example, `args` will be
        `{'arg1': 'value1', 'arg2': 'value 2'}`
        and `content` will be `"... some content ..."`.

        If no named parameters are given like in::

        {{{#!Macro
        ...
        }}}

        then `args` will be `{}`. That makes it possible to
        differentiate the above situation from a call
        made using the macro syntax::

        [[Macro(arg1=value1, arg2="value 2", ... some content...)]]

        in which case `args` will always be `None`.  Here `content`
        will be the `"arg1=value1, arg2="value 2", ... some content..."
        string.
        If like in this example, `content` is expected to contain
        some arguments and named parameters, one can use the
        `parse_args` function to conveniently extract them.
        """
        try:
            processor = IncludeProcessor(self.env, formatter.req)
            out, mime_type = processor.get_include(formatter.context.resource.id, 1, content, args)
        except PermissionException as e:
            if (str(e) == 'INCLUDE_URL'):
                if args is None:
                    self.log.info(
                        'Blocking attempt of %s to include URL by [[%s(%s)]]',
                        formatter.req.authname, name, content)
                else:
                    self.log.info(                                                  # pragma: no cover
                        'Blocking attempt of %s to include URL by {{{#!%s ... }}}', # pragma: no cover
                        formatter.req.authname, name)                               # pragma: no cover

            return ''
        except SystemMessageException as e:
            return system_message(str(e), e.text)
        except IncludeProcessorException as e:  # pragma: no cover: this is just safety nest
            return system_message(str(e))       # pragma: no cover

        # If we have a preview format, lets render it
        if mime_type:
            ctxt = Context.from_request(formatter.req)
            out = Mimeview(self.env).render(ctxt, mime_type, out)
            out = unicode(out)

        # Escape if needed
        if not self.config.getbool('wiki', 'render_unsafe_content', False):
            try:
                out = HTMLParser(StringIO(out)).parse() | HTMLSanitizer()
            except ParseError:
                out = escape(out)

        return out

#----------------------------------------------------------------------
class pystacheMacro(IncludeMacro):
    """
This is an alias of the Include macro, please refer to its documentation
    """

#----------------------------------------------------------------------
# Page name resolution utilities
#----------------------------------------------------------------------
def _resolve_relative_name(wiki_sys, page_name, referrer):
    try:
        return wiki_sys.resolve_relative_name(page_name, referrer)
    except AttributeError:
        pass

    base = referrer.split('/')
    components = page_name.split('/')
    for i, comp in enumerate(components):
        if comp == '..':
            if base:
                base.pop()
        elif comp != '.':
            base.extend(components[i:])
            break
    return '/'.join(base)

def _resolve_scoped_name(wiki_sys, page_name, referrer):
    referrer = referrer.split('/')
    if len(referrer) == 1:           # Non-hierarchical referrer
        return page_name
    # Test for pages with same name, higher in the hierarchy
    for i in range(len(referrer) - 1, 0, -1):
        name = '/'.join(referrer[:i]) + '/' + page_name
        if wiki_sys.has_page(name):
            return name
    if wiki_sys.has_page(page_name):
        return page_name
    # If we are on First/Second/Third, and pagename is Second/Other,
    # resolve to First/Second/Other instead of First/Second/Second/Other
    # See http://trac.edgewall.org/ticket/4507#comment:12
    if '/' in page_name:
        (first, rest) = page_name.split('/', 1)
        for (i, part) in enumerate(referrer):
            if first == part:
                anchor = '/'.join(referrer[:i + 1])
                if wiki_sys.has_page(anchor):
                    return anchor + '/' + rest
    # Assume the user wants a sibling of referrer
    return '/'.join(referrer[:-1]) + '/' + page_name

def _split_path(source_obj):
    if '@' in source_obj:
        path, rev = source_obj.split('@', 1)
    else:
        path, rev = source_obj, None
    return path, rev
