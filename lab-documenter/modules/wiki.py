"""
MediaWiki integration for Lab Documenter

Handles MediaWiki page creation and updates.
"""

import requests
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class MediaWikiUpdater:
    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url
        self.username = username
        self.password = password
        self.session = requests.Session()
    
    def login(self) -> bool:
        """Login to MediaWiki"""
        try:
            login_token = self.session.get(self.api_url, params={
                'action': 'query',
                'meta': 'tokens',
                'type': 'login',
                'format': 'json'
            }).json()['query']['tokens']['logintoken']
            
            response = self.session.post(self.api_url, data={
                'action': 'login',
                'lgname': self.username,
                'lgpassword': self.password,
                'lgtoken': login_token,
                'format': 'json'
            })
            
            return response.json().get('login', {}).get('result') == 'Success'
        except Exception as e:
            logger.error(f"MediaWiki login failed: {e}")
            return False
    
    def update_page(self, title: str, content: str):
        """Update a MediaWiki page"""
        if not self.login():
            return False
            
        try:
            edit_token = self.session.get(self.api_url, params={
                'action': 'query',
                'meta': 'tokens',
                'format': 'json'
            }).json()['query']['tokens']['csrftoken']
            
            response = self.session.post(self.api_url, data={
                'action': 'edit',
                'title': title,
                'text': content,
                'token': edit_token,
                'format': 'json'
            })
            
            return 'error' not in response.json()
        except Exception as e:
            logger.error(f"Failed to update page {title}: {e}")
            return False
    
    def get_page_content(self, title: str) -> str:
        """Get existing page content"""
        try:
            response = self.session.get(self.api_url, params={
                'action': 'query',
                'titles': title,
                'prop': 'revisions',
                'rvprop': 'content',
                'format': 'json'
            })
            
            data = response.json()
            pages = data.get('query', {}).get('pages', {})
            
            for page_id, page_data in pages.items():
                if page_id != '-1':  # Page exists
                    revisions = page_data.get('revisions', [])
                    if revisions:
                        return revisions[0].get('*', '')
            
            return ''  # Page doesn't exist
        except Exception as e:
            logger.warning(f"Failed to get page content for {title}: {e}")
            return ''
    
    def create_index_page(self, title: str, content: str):
        """Create or update the main server index page"""
        if not self.login():
            return False
            
        try:
            # Check if page exists first
            existing_content = self.get_page_content(title)
            if existing_content != content:
                logger.info(f"Updating index page: {title}")
                return self.update_page(title, content)
            else:
                logger.debug(f"Index page {title} is up to date")
                return True
        except Exception as e:
            logger.error(f"Failed to create index page {title}: {e}")
            return False

