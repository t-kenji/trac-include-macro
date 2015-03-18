# -*- coding: utf-8 -*-
#
# Copyright (C) 2006-2013 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.org/wiki/TracLicense.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/log/.
import sys
sys.path.append('..')
from macros import *

from StringIO import StringIO
import unittest
from datetime import datetime
from trac.test import Mock, MockPerm, EnvironmentStub, locale_en
from trac.core import Component, TracError, implements
from trac.util.datefmt import utc
from trac.util.html import html
from trac.util.text import strip_line_ws, to_unicode
from trac.web.chrome import web_context
from trac.web.href import Href
from trac.wiki.api import IWikiSyntaxProvider
from trac.wiki.formatter import HtmlFormatter
from trac.wiki.macros import WikiMacroBase
from trac.wiki.model import WikiPage
from trac.ticket.model import Ticket

#======================================================================
class MockDenyPerm(MockPerm):
    def has_permission(self, action, realm_or_resource=None, id=False,
                       version=False):
        return False

#======================================================================
class WikiTestCase(unittest.TestCase):
    def __init__(self, methodName='runTest', context=None):
        unittest.TestCase.__init__(self, methodName)
        req = Mock(href=Href('/'), abs_href=Href('http://www.example.com/'),
                   chrome={}, session={},
                   authname='anonymous', perm=MockPerm(), tz=utc, args={},
                   locale=locale_en, lc_time=locale_en)
        if context:
            if isinstance(context, tuple):
                context = web_context(req, *context)
        else:
            context = web_context(req, 'wiki', 'WikiStart')
        self.context = context
        all_test_components = []
        self.env = EnvironmentStub(enable=['trac.*'] + all_test_components)
        # -- macros support
        self.env.path = ''
        # -- intertrac support
        self.env.config.set('intertrac', 'trac.title', "Trac's Trac")
        self.env.config.set('intertrac', 'trac.url',
                            "http://trac.edgewall.org")
        self.env.config.set('intertrac', 't', 'trac')
        self.env.config.set('intertrac', 'th.title', "Trac Hacks")
        self.env.config.set('intertrac', 'th.url',
                            "http://trac-hacks.org")
        # -- safe schemes
        self.env.config.set('wiki', 'safe_schemes',
                            'file,ftp,http,https,svn,svn+ssh,'
                            'rfc-2396.compatible,rfc-2396+under_score')
        # TODO: remove the following lines in order to discover
        #       all the places were we should use the req.href
        #       instead of env.href
        self.env.href = req.href
        self.env.abs_href = req.abs_href
        self.req = req

        wiki = WikiPage(self.env)
        wiki.name = 'WikiStart'
        wiki.text = '--'
        wiki.save('joe', 'Entry page', '::1', datetime.now(utc))
        wiki = WikiPage(self.env)
        wiki.name = 'test/page1'
        wiki.text = '--'
        wiki.save('joe', 'Test page', '::1', datetime.now(utc))
        wiki = WikiPage(self.env)
        wiki.name = 'test/second/page1'
        wiki.text = '--'
        wiki.save('joe', 'Test page', '::1', datetime.now(utc))
        wiki = WikiPage(self.env)
        wiki.name = 'test/page2'
        wiki.text = '--'
        wiki.save('joe', 'Test page', '::1', datetime.now(utc))
        ticket = Ticket(self.env)
        wiki = WikiPage(self.env)
        wiki.name = 'test/second'
        wiki.text = '--'
        wiki.save('joe', 'Test page', '::1', datetime.now(utc))
        ticket = Ticket(self.env)
        for f, v in {'reporter':'joe', 'summary':'Test ticket'}.items():
            ticket[f]=v
        ticket.insert()
        ticket.save_changes('joe', 'this is a comment')
        ticket = Ticket(self.env)
        for f, v in {'reporter':'joe', 'summary':'Test ticket'}.items():
            ticket[f]=v
        ticket.insert()


#======================================================================
class TestSystemMessageMacro(unittest.TestCase):
    def setUp(self):
        env = EnvironmentStub()
        self.macro = SystemMessageMacro(env)

    #------------------------------------------------------------------
    def test_wiki_text(self):
        self.assertEquals('[[SystemMessage()]]', SystemMessageMacro.wiki_text(''))
        self.assertEquals('[[SystemMessage(msg)]]', SystemMessageMacro.wiki_text('msg'))
        self.assertEquals('[[SystemMessage(msg, text)]]', SystemMessageMacro.wiki_text('msg','text'))

    #------------------------------------------------------------------
    def test_expand_macro(self):
        formatter = Mock()
        self.assertEquals('<div class="system-message"><strong></strong></div>', str(self.macro.expand_macro(formatter,'SystemMessage','')))
        self.assertEquals('<div class="system-message"><strong>msg</strong></div>', str(self.macro.expand_macro(formatter,'SystemMessage','msg')))
        self.assertEquals('<div class="system-message"><strong>msg</strong><pre>text</pre></div>', str(self.macro.expand_macro(formatter,'SystemMessage','msg,text')))

#======================================================================
class TestPystacheRenderer(unittest.TestCase):
    def setUp(self):
        self.renderer = pystacheRenderer()

    #------------------------------------------------------------------
    def test_escape(self):
        self.assertEquals('!"#$%&\'<>&@;',self.renderer.render('!"#$%&\'<>&@;'))

    #------------------------------------------------------------------
    def test_resolve_partial(self):
        self.assertEquals(u'`{?>foo?}`',self.renderer.render('{{>foo}}'))

    #------------------------------------------------------------------
    def test_resolve_context(self):
        self.assertEquals(u'`{?foo?}`',self.renderer.render('{{foo}}'))
        self.assertEquals(u'bar',self.renderer.render('{{foo}}', {'foo':'bar'}))

#======================================================================
class TestSelfProxy(unittest.TestCase):
    #------------------------------------------------------------------
    def test_constructor(self):
        proxy = SelfProxy('foo', 1)
        self.assertEquals('foo',proxy.url)
        self.assertEquals(u'1',proxy.lineno)
        proxy = SelfProxy('bar', 2)
        self.assertEquals('bar',proxy.url)
        self.assertEquals(u'2',proxy.lineno)

#======================================================================
class TestIncludeProcessor(WikiTestCase):
    def setUp(self):
        WikiTestCase.setUp(self)
        self.req = Mock(perm=MockPerm())
        self.processor = IncludeProcessor(self.env, self.req)

    #==================================================================
    def test_validate_wiki_page(self):
        self.assertEquals([], self.processor.validate_wiki_page(self.req.perm,'')) 
        self.assertEquals([], self.processor.validate_wiki_page(self.req.perm,'[[Include(test)]]')) 
        self.assertEquals([], self.processor.validate_wiki_page(self.req.perm,'{{template}}')) 
        self.assertEquals([], self.processor.validate_wiki_page(MockDenyPerm(),'')) 
        self.assertEquals([(None, 'INCLUDE_CREATE denied.')],
                          self.processor.validate_wiki_page(MockDenyPerm(),'[[Include(test)]]')) 
        self.assertEquals([(None, 'TEMPLATE_CREATE denied.')],
                          self.processor.validate_wiki_page(MockDenyPerm(),'{{template}}')) 
        self.assertEquals([(None, 'INCLUDE_CREATE denied.'), (None, 'TEMPLATE_CREATE denied.')],
                          self.processor.validate_wiki_page(MockDenyPerm(),'[[Include({{template}})]]')) 

    #------------------------------------------------------------------
    def test_constructor(self):
        pass

    #------------------------------------------------------------------
    def test__write(self):
        self.processor.pystache_input_buffer = None
        self.processor.output_buffer = StringIO()

        self.processor._write('foo')
        self.assertEquals('foo',self.processor.output_buffer.getvalue())
        
        self.processor._write('bar')
        self.assertEquals('foobar',self.processor.output_buffer.getvalue())
        
        self.processor.pystache_input_buffer = StringIO()

        self.processor._write('foo')
        self.assertEquals('foo',self.processor.pystache_input_buffer.getvalue())
        self.assertEquals('foobar',self.processor.output_buffer.getvalue())

        self.processor._write('bar')
        self.assertEquals('foobar',self.processor.pystache_input_buffer.getvalue())
        self.assertEquals('foobar',self.processor.output_buffer.getvalue())

    #------------------------------------------------------------------
    def test_setup_pystache_context(self):
        self.processor.urls_stack = [('url',
                                      {'stacked-global-1':'v-global-1'},
                                      {'stacked-local-1':'v-local-1'})]
        self.processor.globals = {'global-2':'v-global-2'}
        self.processor.locals = {'local-2':'v-local-2'}

        self.processor.pystache_context = None
        
        self.processor._setup_pystache_context()

        self.assertIsInstance(self.processor.pystache_context, dict)
        self.assertIn('stacked-global-1', self.processor.pystache_context)
        self.assertEquals('v-global-1', self.processor.pystache_context['stacked-global-1'])
        self.assertNotIn('stacked-local-1', self.processor.pystache_context)
        self.assertIn('global-2', self.processor.pystache_context)
        self.assertEquals('v-global-2', self.processor.pystache_context['global-2'])
        self.assertIn('local-2', self.processor.pystache_context)
        self.assertEquals('v-local-2', self.processor.pystache_context['local-2'])

        self.processor.locals['local-3'] = 'v-local-3'

        self.processor._setup_pystache_context()

        self.assertIsInstance(self.processor.pystache_context, dict)
        self.assertIn('stacked-global-1', self.processor.pystache_context)
        self.assertEquals('v-global-1', self.processor.pystache_context['stacked-global-1'])
        self.assertNotIn('stacked-local-1', self.processor.pystache_context)
        self.assertIn('global-2', self.processor.pystache_context)
        self.assertEquals('v-global-2', self.processor.pystache_context['global-2'])
        self.assertIn('local-2', self.processor.pystache_context)
        self.assertEquals('v-local-2', self.processor.pystache_context['local-2'])
        self.assertNotIn('local-3', self.processor.pystache_context)

        self.processor.pystache_context = None
        
        self.processor._setup_pystache_context()

        self.assertIsInstance(self.processor.pystache_context, dict)
        self.assertIn('stacked-global-1', self.processor.pystache_context)
        self.assertEquals('v-global-1', self.processor.pystache_context['stacked-global-1'])
        self.assertNotIn('stacked-local-1', self.processor.pystache_context)
        self.assertIn('global-2', self.processor.pystache_context)
        self.assertEquals('v-global-2', self.processor.pystache_context['global-2'])
        self.assertIn('local-2', self.processor.pystache_context)
        self.assertEquals('v-local-2', self.processor.pystache_context['local-2'])
        self.assertIn('local-3', self.processor.pystache_context)
        self.assertEquals('v-local-3', self.processor.pystache_context['local-3'])
        
        self.processor.globals['global-4'] = ['v-global-40', 'v-global-41'] 
        self.processor.globals['global-5'] = {'foo':'v-global-50', 'bar':'v-global-51'} 
        self.processor.locals['local-4'] = ['v-local-40', 'v-local-41'] 
        self.processor.locals['local-5'] = {'foo':'v-local-50', 'bar':'v-local-51'} 

        self.processor.pystache_context = None
        
        self.processor._setup_pystache_context()

        self.assertIsInstance(self.processor.pystache_context, dict)
        self.assertIn('stacked-global-1', self.processor.pystache_context)
        self.assertEquals('v-global-1', self.processor.pystache_context['stacked-global-1'])
        self.assertNotIn('stacked-local-1', self.processor.pystache_context)
        self.assertIn('global-2', self.processor.pystache_context)
        self.assertEquals('v-global-2', self.processor.pystache_context['global-2'])
        self.assertIn('local-2', self.processor.pystache_context)
        self.assertEquals('v-local-2', self.processor.pystache_context['local-2'])
        self.assertIn('local-3', self.processor.pystache_context)
        self.assertEquals('v-local-3', self.processor.pystache_context['local-3'])

        self.assertIn('global-4', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['global-4'], list)
        self.assertEquals(2, len(self.processor.pystache_context['global-4']))
        
        self.assertIsInstance(self.processor.pystache_context['global-4'][0], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-4'][0])
        self.assertIn(u'index', self.processor.pystache_context['global-4'][0])
        self.assertIn(u'value', self.processor.pystache_context['global-4'][0])
        self.assertEquals('global-4[0]', self.processor.pystache_context['global-4'][0][u'name'])
        self.assertEquals(0, self.processor.pystache_context['global-4'][0][u'index'])
        self.assertEquals('v-global-40', self.processor.pystache_context['global-4'][0][u'value'])

        self.assertIsInstance(self.processor.pystache_context['global-4'][1], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-4'][1])
        self.assertIn(u'index', self.processor.pystache_context['global-4'][1])
        self.assertIn(u'value', self.processor.pystache_context['global-4'][1])
        self.assertEquals('global-4[1]', self.processor.pystache_context['global-4'][1][u'name'])
        self.assertEquals(1, self.processor.pystache_context['global-4'][1][u'index'])
        self.assertEquals('v-global-41', self.processor.pystache_context['global-4'][1][u'value'])

        self.assertIn('global-5', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['global-5'], list)
        self.assertEquals(2, len(self.processor.pystache_context['global-5']))
        
        self.assertIsInstance(self.processor.pystache_context['global-5'][0], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-5'][0])
        self.assertIn(u'index', self.processor.pystache_context['global-5'][0])
        self.assertIn(u'value', self.processor.pystache_context['global-5'][0])
        self.assertEquals('global-5[foo]', self.processor.pystache_context['global-5'][0][u'name'])
        self.assertEquals('foo', self.processor.pystache_context['global-5'][0][u'index'])
        self.assertEquals('v-global-50', self.processor.pystache_context['global-5'][0][u'value'])

        self.assertIsInstance(self.processor.pystache_context['global-5'][1], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-5'][1])
        self.assertIn(u'index', self.processor.pystache_context['global-5'][1])
        self.assertIn(u'value', self.processor.pystache_context['global-5'][1])
        self.assertEquals('global-5[bar]', self.processor.pystache_context['global-5'][1][u'name'])
        self.assertEquals('bar', self.processor.pystache_context['global-5'][1][u'index'])
        self.assertEquals('v-global-51', self.processor.pystache_context['global-5'][1][u'value'])

        self.assertIn('local-4', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['local-4'], list)
        self.assertEquals(2, len(self.processor.pystache_context['local-4']))
        
        self.assertIsInstance(self.processor.pystache_context['local-4'][0], dict)
        self.assertIn(u'name', self.processor.pystache_context['local-4'][0])
        self.assertIn(u'index', self.processor.pystache_context['local-4'][0])
        self.assertIn(u'value', self.processor.pystache_context['local-4'][0])
        self.assertEquals('local-4[0]', self.processor.pystache_context['local-4'][0][u'name'])
        self.assertEquals(0, self.processor.pystache_context['local-4'][0][u'index'])
        self.assertEquals('v-local-40', self.processor.pystache_context['local-4'][0][u'value'])

        self.assertIsInstance(self.processor.pystache_context['local-4'][1], dict)
        self.assertIn(u'name', self.processor.pystache_context['local-4'][1])
        self.assertIn(u'index', self.processor.pystache_context['local-4'][1])
        self.assertIn(u'value', self.processor.pystache_context['local-4'][1])
        self.assertEquals('local-4[1]', self.processor.pystache_context['local-4'][1][u'name'])
        self.assertEquals(1, self.processor.pystache_context['local-4'][1][u'index'])
        self.assertEquals('v-local-41', self.processor.pystache_context['local-4'][1][u'value'])

        self.assertIn('local-5', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['local-5'], list)
        self.assertEquals(2, len(self.processor.pystache_context['local-5']))
        
        self.assertIsInstance(self.processor.pystache_context['local-5'][0], dict)
        self.assertIn(u'name', self.processor.pystache_context['local-5'][0])
        self.assertIn(u'index', self.processor.pystache_context['local-5'][0])
        self.assertIn(u'value', self.processor.pystache_context['local-5'][0])
        self.assertEquals('local-5[foo]', self.processor.pystache_context['local-5'][0][u'name'])
        self.assertEquals('foo', self.processor.pystache_context['local-5'][0][u'index'])
        self.assertEquals('v-local-50', self.processor.pystache_context['local-5'][0][u'value'])

        self.assertIsInstance(self.processor.pystache_context['local-5'][1], dict)
        self.assertIn(u'name', self.processor.pystache_context['local-5'][1])
        self.assertIn(u'index', self.processor.pystache_context['local-5'][1])
        self.assertIn(u'value', self.processor.pystache_context['local-5'][1])
        self.assertEquals('local-5[bar]', self.processor.pystache_context['local-5'][1][u'name'])
        self.assertEquals('bar', self.processor.pystache_context['local-5'][1][u'index'])
        self.assertEquals('v-local-51', self.processor.pystache_context['local-5'][1][u'value'])

        self.processor.urls_stack.append(('url',
                                          self.processor.globals,
                                          self.processor.locals))
        self.processor.globals = dict()
        self.processor.locals = dict()
        self.processor.pystache_context = None
        
        self.processor._setup_pystache_context()

        self.assertIsInstance(self.processor.pystache_context, dict)
        self.assertIn('stacked-global-1', self.processor.pystache_context)
        self.assertEquals('v-global-1', self.processor.pystache_context['stacked-global-1'])
        self.assertNotIn('stacked-local-1', self.processor.pystache_context)
        self.assertIn('global-2', self.processor.pystache_context)
        self.assertEquals('v-global-2', self.processor.pystache_context['global-2'])

        self.assertIn('global-4', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['global-4'], list)
        self.assertEquals(2, len(self.processor.pystache_context['global-4']))
        
        self.assertIsInstance(self.processor.pystache_context['global-4'][0], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-4'][0])
        self.assertIn(u'index', self.processor.pystache_context['global-4'][0])
        self.assertIn(u'value', self.processor.pystache_context['global-4'][0])
        self.assertEquals('global-4[0]', self.processor.pystache_context['global-4'][0][u'name'])
        self.assertEquals(0, self.processor.pystache_context['global-4'][0][u'index'])
        self.assertEquals('v-global-40', self.processor.pystache_context['global-4'][0][u'value'])

        self.assertIsInstance(self.processor.pystache_context['global-4'][1], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-4'][1])
        self.assertIn(u'index', self.processor.pystache_context['global-4'][1])
        self.assertIn(u'value', self.processor.pystache_context['global-4'][1])
        self.assertEquals('global-4[1]', self.processor.pystache_context['global-4'][1][u'name'])
        self.assertEquals(1, self.processor.pystache_context['global-4'][1][u'index'])
        self.assertEquals('v-global-41', self.processor.pystache_context['global-4'][1][u'value'])

        self.assertIn('global-5', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['global-5'], list)
        self.assertEquals(2, len(self.processor.pystache_context['global-5']))
        
        self.assertIsInstance(self.processor.pystache_context['global-5'][0], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-5'][0])
        self.assertIn(u'index', self.processor.pystache_context['global-5'][0])
        self.assertIn(u'value', self.processor.pystache_context['global-5'][0])
        self.assertEquals('global-5[foo]', self.processor.pystache_context['global-5'][0][u'name'])
        self.assertEquals('foo', self.processor.pystache_context['global-5'][0][u'index'])
        self.assertEquals('v-global-50', self.processor.pystache_context['global-5'][0][u'value'])

        self.assertIsInstance(self.processor.pystache_context['global-5'][1], dict)
        self.assertIn(u'name', self.processor.pystache_context['global-5'][1])
        self.assertIn(u'index', self.processor.pystache_context['global-5'][1])
        self.assertIn(u'value', self.processor.pystache_context['global-5'][1])
        self.assertEquals('global-5[bar]', self.processor.pystache_context['global-5'][1][u'name'])
        self.assertEquals('bar', self.processor.pystache_context['global-5'][1][u'index'])
        self.assertEquals('v-global-51', self.processor.pystache_context['global-5'][1][u'value'])
        
    #------------------------------------------------------------------
    def test_redirect_to_pystache(self):
        self.processor.pystache_input_buffer = None
        self.processor.output_buffer = StringIO()

        self.processor._write('foo')
        
        self.processor._redirect_to_pystache('foo', 1, True)
        self.assertIsInstance(self.processor.pystache_input_buffer, StringIO)
        self.assertIsInstance(self.processor.pystache_context, dict)
        self.assertIn('self', self.processor.pystache_context)
        self.assertIsInstance(self.processor.pystache_context['self'], SelfProxy)
        self.assertEquals('foo', self.processor.pystache_context['self'].url)
        self.assertEquals(u'1', self.processor.pystache_context['self'].lineno)
        
        self.processor._write('{{bar}}')

        self.processor._redirect_to_pystache('bar', 2, False)

        self.assertEquals(u'foo`{?bar?}`',self.processor.output_buffer.getvalue())
        self.assertIs(self.processor.pystache_input_buffer, None)

    #------------------------------------------------------------------
    def test_process_arguments(self):
        self.processor.globals = {'mime_type':'text/plain'}
        self.processor.locals = {}
        
        url, mime_type = self.processor._process_arguments([], {}, False)
        self.assertIs(url, None)
        self.assertIs(mime_type, None)
        self.assertEquals(self.processor.globals, {'mime_type':'text/plain'})
        self.assertEquals(self.processor.locals, {'argv':[]})

        url, mime_type = self.processor._process_arguments([], {}, True)
        self.assertIs(url, None)
        self.assertEquals(mime_type, 'text/plain')
        self.assertEquals(self.processor.globals, {'mime_type': 'text/plain'})
        self.assertEquals(self.processor.locals, {'argv':[]})

        self.processor.urls_stack.append(('foo', self.processor.globals, self.processor.locals))
        self.processor.globals = {}
        self.processor.locals = {}

        url, mime_type = self.processor._process_arguments([], {}, True)
        self.assertIs(url, None)
        self.assertIsNot(mime_type, None)
        self.assertEquals(mime_type, 'text/plain')
        self.assertEquals(self.processor.globals, {'mime_type': 'text/plain'})
        self.assertEquals(self.processor.locals, {'argv':[]})

        self.processor.urls_stack = [] 
        self.processor.globals = {'mime_type' : 'text/plain'}
        self.processor.locals = {}

        url, mime_type = self.processor._process_arguments([], {}, True)
        self.assertIs(url, None)
        self.assertEquals(mime_type, 'text/plain')
        self.assertEquals(self.processor.globals, {'mime_type': 'text/plain'})
        self.assertEquals(self.processor.locals, {'argv':[]})

        self.processor.globals = {'mime_type': 'text/plain'}
        self.processor.locals = {}
        
        url, mime_type = self.processor._process_arguments(['test1'], {}, False)
        self.assertEquals(url, 'test1')
        self.assertIs(mime_type, None)
        self.assertEquals(self.processor.globals, {'src': 'test1', 'mime_type': 'text/plain'})
        self.assertEquals(self.processor.locals, {'argv':['test1']})

        self.processor.globals = {'mime_type': 'text/plain'}
        self.processor.locals = {}
        
        url, mime_type = self.processor._process_arguments(['test2', 'text/plain'], {}, False)
        self.assertEquals(url, 'test2')
        self.assertEquals(mime_type, 'text/plain')
        self.assertEquals(self.processor.globals, {'src': 'test2', 'mime_type' : 'text/plain'})
        self.assertEquals(self.processor.locals, {'argv':['test2', 'text/plain']})

        self.processor.globals = {'mime_type': 'text/plain'}
        self.processor.locals = {}

        url, mime_type = self.processor._process_arguments([], {'src':'test1','mime_type':'text/x-trac-wiki'}, False)
        self.assertEquals(url, 'test1')
        self.assertIs(mime_type, 'text/x-trac-wiki')
        self.assertEquals(self.processor.globals, {'src': 'test1', 'mime_type' : 'text/x-trac-wiki'})
        self.assertEquals(self.processor.locals, {'argv':[]})

        self.processor.globals = {}
        self.processor.locals = {}

        url, mime_type = self.processor._process_arguments(['argv0', 'argv1'], {'src':'test1','mime_type':'text/x-trac-wiki'}, False)
        self.assertEquals(url, 'test1')
        self.assertIs(mime_type, 'text/x-trac-wiki')
        self.assertEquals(self.processor.globals, {'src': 'test1', 'mime_type' : 'text/x-trac-wiki'})
        self.assertEquals(self.processor.locals, {'argv':['argv0', 'argv1']})

    #------------------------------------------------------------------
    def test_proxy_execute(self):
        pass
    
    #------------------------------------------------------------------
    def test_process_execute(self):
        pass
        
    #------------------------------------------------------------------
    def test_proxy_include(self):
        pass
    
    #------------------------------------------------------------------
    def test_process_include(self):
        pass

    #------------------------------------------------------------------
    def test_prepare_text(self):
        pass

    #==================================================================
    def test_prepare_wiki_page(self):
        with self.assertRaises(SystemMessageException): 
            self.processor.prepare_wiki_page(None, '')

        self.assertEquals('', self.processor.prepare_wiki_page('None', None))
        
        TEST_TEMPL1='''
== Parameters replacements

* named named1 = {{named1}}
* named named2 = {{named2}}
* listed argv[0] = {{argv[0]}}
* listed argv[1] = {{argv[1]}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
* named src = {{src}}
* named mime_type = {{mime_type}}
* automatic self.url = {{self.url}}
* automatic self.lineno = {{self.lineno}}
* automatic req = {{req}}
* automatic env = {{env}}

== In line bloc processor

`{{{#!pystache 10+27,mime_type=text/x-trac-wiki`
{{{#!pystache 10+27,mime_type=text/x-trac-wiki
* 10+27 = {{argv[0]}}
}}}

=== Scoped parameters with block processors

`{{{#!pystache scope-global,param-global,named1=templ1-named1,mime_type=text/x-trac-wiki`

{{{#!pystache scope-global,param-global,named1=templ1-named1,mime_type=text/x-trac-wiki
* named named1 = {{named1}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
----
`{{{#!pystache`
{{{#!pystache
* named named1 = {{named1}}
* all listed argv:
    {{#argv}}
    - {{name}} = {{value}}
    {{/argv}}
}}}
`}}}`

`{{{#!pystache scope0,param0`
{{{#!pystache scope0,param0
  * named named1 = {{named1}}
  * all listed argv:
    {{#argv}}
    - {{name}} = {{value}}
    {{/argv}}
}}}
`}}}`

`{{{#!pystache scope1,param1,named1=templ1-named1-scope1`
{{{#!pystache scope1,param1,named1=templ1-named1-scope1
  * named named1 = {{named1}}
  * all listed argv:
    {{#argv}}
    - {{name}} = {{value}}
    {{/argv}}
}}}
`}}}`

`{{{#!pystache scope2,param2,named1=templ1-named1-scope2`
{{{#!pystache scope2,param2,named1=templ1-named1-scope2
* named named1 = {{named1}}
* all listed argv:
    {{#argv}}
    - {{name}} = {{value}}
    {{/argv}}
  }}}
`}}}`
----
{{{
{{{#!pystache test=failure of
In preformated block a new include is not evaluated.
Hence this {{test}} replacement.
Nevertheless, previously active replacement still
work.
* named named1 = {{named1}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
}}}
}}}
----
* named named1 = {{named1}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
}}}
`}}}`

=== MIME type == text/x-trac-wiki
{{{
{{{#!pystache author=me,mime_type=text/x-trac-wiki
this is written by {{author}}
}}}
}}}

{{{#!pystache author=me,mime_type=text/x-trac-wiki
this is written by {{author}}
}}}

=== MIME type != text/x-trac-wiki
{{{
{{{#!pystache author=me,mime_type=text
this is written by {{author}}
}}}
}}}

{{{#!pystache author=me,mime_type=text
this is written by {{author}}
}}}

== Recursive include

=== Basic case: recurse itself

{{{
[[Include(test/templ1)]]
}}}

[[Include(test/templ1)]]

=== Complex case: recurse via another page

{{{
[[Include(test/include_templ1)]]
}}}

[[Include(test/include_templ1)]]

== Basic includes

`[[Include(scope-global,param-global,named1=templ1-named1,src=test/templ2,mime_type=text/x-trac-wiki)]]`
[[Include(scope-global,param-global,named1=templ1-named1,src=test/templ2,mime_type=text/x-trac-wiki)]]

`[[Include(test/templ2,scope-global,param-global,text/x-trac-wiki,named1=templ1-named1)]]`
[[Include(test/templ2,scope-global,param-global,text/x-trac-wiki,named1=templ1-named1)]]

`[[Include(test/templ2,scope-global,param-global,named1=templ1-named1)]]`
[[Include(test/templ2,scope-global,param-global,named1=templ1-named1)]]

`[[Include(test/templ2,scope-global,param-global,named1=templ1-named1,)]]`
[[Include(test/templ2,scope-global,param-global,named1=templ1-named1,)]]

=== Scoped parameters with includes

`{{{#!pystache scope-global,param-global,named1=templ1-named1,named2=templ1-named2,mime_type=text/x-trac-wiki`

{{{#!pystache scope-global,param-global,named1=templ1-named1,named2=templ1-named2,mime_type=text/x-trac-wiki
* named named1 = {{named1}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
----
`[[Include(test/templ3)]]`
[[Include(test/templ3)]]
`{{{ [[Include(test/templ3)]] }}}`
{{{
[[Include(test/templ3)]]
}}}
----
`{{{#!Include`

{{{#!Include
* named named1 = {{named1}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
}}}
----
* named named1 = {{named1}}
* all listed argv:
  {{#argv}}
  - {{name}} = {{value}}
  {{/argv}}
}}}
`}}}`
'''
        text = self.processor.prepare_wiki_page('test/templ1', TEST_TEMPL1)

        TEST_PARTIAL1='''
{{{#!pystache 10+27,mime_type=text/x-trac-wiki
'''
        text = self.processor.prepare_wiki_page('test/partial1', TEST_PARTIAL1)

        self.assertEquals(TEST_PARTIAL1, text) 
    
        TEST_TEMPL2='''
{{{#!pystache
}}}
'''
        text = self.processor.prepare_wiki_page('test/partial2', TEST_TEMPL2)

        self.assertEquals('\n\n', text) 
   
        TEST_TEMPL3='[[Include(WikiStart,text/plain)]]'
        text = self.processor.prepare_wiki_page('test/templ3', TEST_TEMPL3)

        self.assertEquals('[[Include(WikiStart,text/plain)]]', text) 
   
        TEST_PARTIAL3='''
[[Include(http://www.google.com)]]
'''
        text = self.processor.prepare_wiki_page('test/partial3', TEST_PARTIAL3)

        self.assertEquals(TEST_PARTIAL3, text) 

        TEST_PARTIAL4='''
{{{#!pystache }}}
'''
        text = self.processor.prepare_wiki_page('test/partial4', TEST_PARTIAL4)

        self.assertEquals(TEST_PARTIAL4, text) 

    #------------------------------------------------------------------
    def test_get_url(self):
        pass

    #------------------------------------------------------------------
    def test_get_page(self):
        pass

    #------------------------------------------------------------------
    def test_get_source(self):
        pass

    #------------------------------------------------------------------
    def test_get_ticket(self):
        pass

    #------------------------------------------------------------------
    def test_get_include_intern(self):
        with self.assertRaises(SystemMessageException):
            self.processor._get_include('foo', None, None)

    #==================================================================
    def test_get_include(self):
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include(None, 1, '', None)
        self.assertEquals('resource id is None', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, '', [])
        self.assertEquals('type(args) is not dict', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, '', None)
        self.assertEquals('source parameter is missing', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'WikiStart@314159', None)
        self.assertEquals('No version "314159" for wiki page "WikiStart"', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'http://', None)
        self.assertEquals('Error while retrieving file: "<urlopen error no host given>"', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'test/page', None)
        self.assertEquals('Wiki page "test/page" does not exist', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, '/test/page', None)
        self.assertEquals('Wiki page "test/page" does not exist', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, './test/page', None)
        self.assertEquals('Wiki page "foo/test/page" does not exist', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, '../test/page', None)
        self.assertEquals('Wiki page "test/page" does not exist', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'source:bar', None)
        self.assertEquals('Repository for "bar" is not accessible', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:314159', None)
        self.assertEquals('Ticket field must be specified', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:', None)
        self.assertEquals('Ticket field must be specified', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:1', None)
        self.assertEquals('Ticket field must be specified', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:1:summary', None)
        self.assertEquals('Malformed ticket field "summary"', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:1:comment', None)
        self.assertEquals('Malformed ticket field "comment"', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:1:summary:0', None)
        self.assertEquals('Unsupported ticket field "summary"', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:1:comment:0', None)
        self.assertEquals('Comment 0 does not exist for Ticket 1', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:-34159:comment:1', None)
        self.assertEquals('"-34159" is not a valid ticket id', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:2:comment:1', None)
        self.assertEquals('Could not resolve ticket:2:comment:1', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:foo:comment', None)
        self.assertEquals('"foo" is not a valid ticket id', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'ticket:314159:comment:0', None)
        self.assertEquals('Ticket "314159" does not exist', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('foo', 1, 'bar://', None)
        self.assertEquals('Unsupported realm bar', str(cm.exception))
        with self.assertRaises(SystemMessageException) as cm:
            self.processor.get_include('test/second/page1', 1, 'second/page2', None)
        self.assertEquals('Wiki page "test/second/page2" does not exist', str(cm.exception))

        text, mime_type = self.processor.get_include('foo', 1, 'http://www.google.com', None)
        text, mime_type = self.processor.get_include('foo', 1, 'WikiStart', None)
        text, mime_type = self.processor.get_include('foo', 1, 'WikiStart@1', None)
        text, mime_type = self.processor.get_include('WikiStart', 1, 'test/page1', None)
        text, mime_type = self.processor.get_include('test/page1', 1, '../../WikiStart', None)
        text, mime_type = self.processor.get_include('test/page1', 1, 'page2', None)
        text, mime_type = self.processor.get_include('test/page1', 1, '/WikiStart', None)
        text, mime_type = self.processor.get_include('test/page1', 1, 'WikiStart', None)
        text, mime_type = self.processor.get_include('test/page1', 1, 'WikiStart,text/plain', None)
        text, mime_type = self.processor.get_include('foo', 1, 'ticket:1:comment:1', None)
        text, mime_type = self.processor.get_include('foo', 1, None, {'src':'test/none'})
        text, mime_type = self.processor.get_include('foo', 1, '', {'src':'test/empty'})
            
#======================================================================
class TestIncludeMacro(WikiTestCase):
    def setUp(self):
        self.macro = IncludeMacro(self.env)

    def test_get_permission_actions(self):
        self.assertEquals(['INCLUDE_URL', 'INCLUDE_CREATE', 'TEMPLATE_CREATE'],
                          list(self.macro.get_permission_actions()))

    def test_prepare_wiki_page(self):
        page = WikiPage(self.env, 'test/page1')
        fields={'text':page.text}
        self.macro.prepare_wiki_page(self.req, page, fields)
        self.assertEquals('--', fields['text'])
        fields={'text':'[[Include(test/page1)]]'}
        self.macro.prepare_wiki_page(self.req, page, fields)
        self.assertEquals('[[SystemMessage(Include failed, wiki://test/page1:1:Recursion in "wiki://test/page1" detected)]]', fields['text'])
        page1 = WikiPage(self.env)
        fields={'text':''}
        self.macro.prepare_wiki_page(self.req, page1, fields)
        self.assertEquals('[[SystemMessage(resource id is None)]]', fields['text'])

        self.req.perm =MockDenyPerm()
        page = WikiPage(self.env, 'test/page1')
        fields={'text':'[[Include(http://www.google.com,text/x-trac-wiki)]]'}
        self.macro.prepare_wiki_page(self.req, page, fields)
        self.assertEquals('', fields['text'])
        
    def test_validate_wiki_page(self):
        page = WikiPage(self.env, 'test/page1')
        self.macro.validate_wiki_page(self.req, page)

    def test_expand_macro(self):
        formatter = HtmlFormatter(self.env, self.context, '')
        formatter.req = self.req

        self.macro.expand_macro(formatter,'Include','test/page1')
        self.macro.expand_macro(formatter,'Include','http://www.google.com')
        self.macro.expand_macro(formatter,'Include','foo')

        self.req.perm = MockDenyPerm()
        self.macro.expand_macro(formatter,'Include','http://a_denied_url')
        self.macro.expand_macro(formatter,'Include','source:a_denied_file')
        
if __name__ == '__main__':
    unittest.main()
