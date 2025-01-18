# frontend/components/sidebar.py

import streamlit as st
import yaml
from pathlib import Path
import logging
from typing import Dict, Any, Optional
from threading import Lock
import subprocess
import sys
import tempfile
import os
from backend.core.crawler import RepositoryCrawler

logger = logging.getLogger(__name__)

def show_file_dialog():
    """Run file dialog in a separate process to prevent freezing."""
    # Create a temporary Python script for the file dialog
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write('''
import tkinter as tk
from tkinter import filedialog
import sys
import os

if __name__ == "__main__":
    root = tk.Tk()
    root.attributes('-alpha', 0.0)  # Make window fully transparent
    root.attributes('-topmost', 1)  # Keep on top
    root.focus_force()  # Force focus
    
    try:
        # On Windows, we need to lift the window and process events
        root.lift()
        root.update()
        
        # Show the dialog
        path = filedialog.askdirectory(
            parent=root,
            title="Select Repository Directory",
            initialdir=os.path.expanduser("~")  # Start from user's home directory
        )
        
        # Print selected path if any
        if path:
            print(path)
            
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
    finally:
        root.destroy()
''')
        temp_script = f.name

    try:
        # Run the script in a separate process
        result = subprocess.run(
            [sys.executable, temp_script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW  # Windows-specific: prevent console window
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        elif result.stderr:
            logger.error(f"File dialog error: {result.stderr}")
            st.error(f"File dialog error: {result.stderr}")
        return None
        
    except subprocess.TimeoutExpired:
        logger.error("File dialog timed out")
        st.error("File dialog timed out. Please try again.")
        return None
    except Exception as e:
        logger.error(f"Error in file dialog: {str(e)}")
        st.error(f"Error opening file browser: {str(e)}")
        return None
    finally:
        try:
            os.unlink(temp_script)
        except:
            pass

class SidebarComponent:
    _instance = None
    _config_lock = Lock()
    
    # Available LLM providers
    LLM_PROVIDERS = {
        "OpenAI": {
            "models": ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo"],
            "key_name": "OPENAI_API_KEY",
            "supports_multiple_keys": True  # Flag to indicate multiple keys support
        },
        "Anthropic": {
            "models": ["claude-2.1", "claude-instant"],
            "key_name": "ANTHROPIC_API_KEY",
            "supports_multiple_keys": True
        },
        "DeepSeek": {
            "models": ["deepseek-chat"],
            "key_name": "DEEPSEEK_API_KEY",
            "supports_multiple_keys": True
        },
        "Gemini": {
            "models": ["gemini-1.5-pro-latest"],
            "key_name": "GEMINI_API_KEY",
            "is_coordinator": True,  # Flag to indicate this is used for coordination
            "supports_multiple_keys": True
        }
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SidebarComponent, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            logger.info("Initializing SidebarComponent")
            self.default_config = {
                'local_root': '',
                'ignore_patterns': {
                    'directories': [],
                    'files': []
                },
                'llm_provider': 'OpenAI',
                'model': 'gpt-4',
                'api_keys': {}
            }
            self.initialize_state()
            self._initialized = True

    def initialize_state(self):
        """Initialize session state for sidebar with proper locking."""
        with self._config_lock:
            # Initialize loaded_rules if not present
            if 'loaded_rules' not in st.session_state:
                st.session_state.loaded_rules = {}

            # Ensure 'loaded_config' always exists
            if 'loaded_config' not in st.session_state:
                st.session_state.loaded_config = None

            # Also ensure 'config' exists in session state with all required fields
            if 'config' not in st.session_state:
                config_path = Path('config/config.yaml')
                if config_path.exists():
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            loaded_config = yaml.safe_load(f)
                            if loaded_config and isinstance(loaded_config, dict):
                                # Ensure api_keys exists
                                if 'api_keys' not in loaded_config:
                                    loaded_config['api_keys'] = {}
                                st.session_state.config = loaded_config
                                st.session_state.loaded_config = loaded_config
                            else:
                                st.session_state.config = self.default_config.copy()
                    except Exception as e:
                        logger.error(f"Error loading config: {str(e)}")
                        st.session_state.config = self.default_config.copy()
                else:
                    st.session_state.config = self.default_config.copy()
            
            # Ensure api_keys exists in current config
            if 'api_keys' not in st.session_state.config:
                st.session_state.config['api_keys'] = {}

    def validate_repo_path(self, path: str) -> Optional[Path]:
        """Validate and normalize repository path.
        
        Args:
            path: The repository path to validate
            
        Returns:
            Normalized Path object if valid, None otherwise
        """
        try:
            # Convert to absolute path and normalize
            abs_path = Path(path).resolve()
            
            # Basic security checks
            if not abs_path.exists():
                st.error("Repository path does not exist")
                return None
            if not abs_path.is_dir():
                st.error("Path is not a directory")
                return None
                
            # Check for directory traversal attempts
            if ".." in str(abs_path.relative_to(abs_path.anchor)):
                st.error("Invalid repository path: directory traversal not allowed")
                return None
                
            # Additional security checks
            if any(part.startswith('.') for part in abs_path.parts[1:]):
                st.error("Invalid repository path: hidden directories not allowed")
                return None
                
            return abs_path
            
        except Exception as e:
            st.error(f"Invalid repository path: {str(e)}")
            logger.error(f"Path validation error: {str(e)}", exc_info=True)
            return None

    def render(self):
        """Render the sidebar."""
        with st.sidebar:
            st.title("Repository Crawler 🔍")
            settings_tab, files_tab = st.tabs(["Settings", "Files"])

            with settings_tab:
                # LLM Settings Section
                st.markdown("### LLM Settings")
                provider = st.selectbox(
                    "Select LLM Provider",
                    options=list(self.LLM_PROVIDERS.keys()),
                    key="llm_provider"
                )
                
                available_models = self.LLM_PROVIDERS[provider]["models"]
                model = st.selectbox(
                    "Select Model",
                    options=available_models,
                    key="llm_model"
                )
                
                key_name = self.LLM_PROVIDERS[provider]["key_name"]
                supports_multiple = self.LLM_PROVIDERS[provider].get("supports_multiple_keys", False)
                
                # Get existing keys for this provider
                existing_keys = st.session_state.config.get('api_keys', {}).get(key_name, [])
                if not isinstance(existing_keys, list):
                    existing_keys = [existing_keys] if existing_keys else []
                
                # Show existing keys
                if existing_keys:
                    st.markdown("#### Configured API Keys")
                    for i, key in enumerate(existing_keys):
                        masked_key = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "****"
                        col1, col2 = st.columns([0.8, 0.2])
                        with col1:
                            st.text(f"Key {i+1}: {masked_key}")
                        with col2:
                            if st.button("❌", key=f"remove_key_{i}", help=f"Remove key {i+1}"):
                                existing_keys.pop(i)
                                if not existing_keys:  # If last key removed
                                    st.session_state.config['api_keys'][key_name] = []
                                else:
                                    st.session_state.config['api_keys'][key_name] = existing_keys
                                self.save_config(st.session_state.config)
                                st.rerun()
                
                # Add new key section
                st.markdown("#### Add New API Key")
                new_api_key = st.text_input(
                    f"Enter {key_name}",
                    type="password",
                    key=f"new_api_key_{provider}"
                )
                
                if new_api_key:
                    try:
                        # Validate API key before saving
                        if provider == "OpenAI":
                            if not new_api_key.startswith("sk-"):
                                st.error("Invalid OpenAI API key format. Should start with 'sk-'")
                                return repo_path
                            # Test the API key
                            from openai import OpenAI
                            client = OpenAI(api_key=new_api_key)
                            try:
                                # Make a minimal API call to validate the key
                                client.models.list()
                                valid_key = True
                            except Exception as e:
                                st.error(f"Invalid OpenAI API key: {str(e)}")
                                return repo_path
                        elif provider == "Anthropic" and not new_api_key.startswith("sk-ant-"):
                            st.error("Invalid Anthropic API key format. Should start with 'sk-ant-'")
                            return repo_path
                        elif provider == "DeepSeek":
                            # Test the DeepSeek API key
                            try:
                                from openai import OpenAI
                                client = OpenAI(api_key=new_api_key, base_url="https://api.deepseek.com/v1")
                                # Make a minimal API call to validate the key
                                response = client.models.list()
                                valid_key = True
                            except Exception as e:
                                st.error(f"Invalid DeepSeek API key: {str(e)}")
                                return repo_path
                        
                        # Only save if validation passed
                        if 'api_keys' not in st.session_state.config:
                            st.session_state.config['api_keys'] = {}
                        
                        # Initialize as list if not already
                        if key_name not in st.session_state.config['api_keys']:
                            st.session_state.config['api_keys'][key_name] = []
                        elif not isinstance(st.session_state.config['api_keys'][key_name], list):
                            # Convert single key to list
                            old_key = st.session_state.config['api_keys'][key_name]
                            st.session_state.config['api_keys'][key_name] = [old_key] if old_key else []
                        
                        # Add new key if not already present
                        if new_api_key not in st.session_state.config['api_keys'][key_name]:
                            st.session_state.config['api_keys'][key_name].append(new_api_key)
                            st.session_state.config['llm_provider'] = provider
                            st.session_state.config['model'] = model
                            self.save_config(st.session_state.config)
                            st.success(f"{provider} API key added successfully!")
                            st.rerun()
                        else:
                            st.warning("This API key is already configured.")
                            
                    except Exception as e:
                        st.error(f"Error saving API key: {str(e)}")
                        logger.error(f"API key save error: {str(e)}", exc_info=True)
                        return repo_path

                # Repository Section
                st.markdown("### Repository")
                repo_path = st.text_input(
                    "Path",
                    value=st.session_state.config.get('local_root', ''),
                    help="Enter the full path to your local repository",
                    placeholder="C:/path/to/repository"
                )

                if st.button("📂 Browse for Repository", help="Browse for repository directory", use_container_width=True):
                    try:
                        selected_path = show_file_dialog()
                        if selected_path:
                            # Validate selected path
                            validated_path = self.validate_repo_path(selected_path)
                            if validated_path:
                                repo_path = str(validated_path)
                                st.session_state.config['local_root'] = repo_path
                                self.save_config(st.session_state.config)
                                st.rerun()
                    except Exception as e:
                        st.error(f"Error opening file browser: {str(e)}")
                        logger.error(f"File browser error: {str(e)}", exc_info=True)

                # Validate manually entered path
                if repo_path != st.session_state.config.get('local_root', ''):
                    validated_path = self.validate_repo_path(repo_path)
                    if validated_path:
                        repo_path = str(validated_path)
                        st.session_state.config['local_root'] = repo_path
                        self.save_config(st.session_state.config)

                # Configuration Section
                st.markdown("### Configuration")

                # Safe check for loaded_config
                if st.session_state.loaded_config:  # If not None, show success or clear button
                    st.success(f"Using config: {st.session_state.loaded_config}")
                    if st.button("❌ Clear All", key="clear_all_config"):
                        self.clear_state()
                        st.rerun()

                if st.session_state.loaded_rules:
                    st.write("Loaded rules found:", list(st.session_state.loaded_rules.keys()))
                    for filename in list(st.session_state.loaded_rules.keys()):
                        col1, col2 = st.columns([0.8, 0.2])
                        with col1:
                            st.markdown(f"- {filename}")
                        with col2:
                            if st.button("❌", key=f"remove_rule_{filename}", help=f"Remove {filename}"):
                                del st.session_state.loaded_rules[filename]
                                st.rerun()

                uploaded_files = st.file_uploader(
                    "Upload system files",
                    type=['yaml', 'yml', 'md', 'txt', 'cursorrules'],
                    help="Upload config.yaml, system instructions, prompt, or rule files",
                    accept_multiple_files=True,
                    key=f"config_uploader_{len(st.session_state.loaded_rules)}"
                )
                if uploaded_files:
                    for uploaded_file in uploaded_files:
                        if uploaded_file.name.endswith(('.yaml', '.yml')):
                            if self.load_config_file(uploaded_file):
                                st.session_state.loaded_config = uploaded_file.name
                                st.success("Configuration loaded successfully!")
                        elif uploaded_file.name not in st.session_state.loaded_rules:
                            content = uploaded_file.getvalue().decode('utf-8')
                            st.session_state.loaded_rules[uploaded_file.name] = content
                            st.success(f"Rule file loaded: {uploaded_file.name}")

                # Ignore Patterns Section
                st.markdown("### Ignore Patterns")

                with st.expander("Directories", expanded=False):
                    dirs = st.session_state.config.get('ignore_patterns', {}).get('directories', [])
                    dirs_text = st.text_area(
                        "Edit directories to ignore (one per line)",
                        value="\n".join(dirs) if dirs else "",
                        height=200,
                        label_visibility="collapsed",
                        key="ignore_dirs"
                    )
                    if dirs_text != "\n".join(dirs):
                        new_dirs = [d.strip() for d in dirs_text.split("\n") if d.strip()]
                        st.session_state.config['ignore_patterns']['directories'] = new_dirs
                        current_config = st.session_state.config.copy()
                        self.save_config(current_config)
                        if 'current_tree' in st.session_state:
                            del st.session_state.current_tree
                        st.rerun()

                with st.expander("Files", expanded=False):
                    files = st.session_state.config.get('ignore_patterns', {}).get('files', [])
                    files_text = st.text_area(
                        "Edit files to ignore (one per line)",
                        value="\n".join(files) if files else "",
                        height=200,
                        label_visibility="collapsed",
                        key="ignore_files"
                    )
                    if files_text != "\n".join(files):
                        new_files = [f.strip() for f in files_text.split("\n") if f.strip()]
                        st.session_state.config['ignore_patterns']['files'] = new_files
                        current_config = st.session_state.config.copy()
                        self.save_config(current_config)
                        if 'current_tree' in st.session_state:
                            del st.session_state.current_tree
                        st.rerun()

            with files_tab:
                if repo_path:
                    # Validate path before using
                    validated_path = self.validate_repo_path(repo_path)
                    if validated_path and validated_path.exists():
                        try:
                            # Initialize crawler with current config
                            crawler = RepositoryCrawler(str(validated_path), st.session_state.config)
                            file_tree = crawler.get_file_tree()
                            
                            # Get current ignore patterns
                            ignored_dirs = set(st.session_state.config.get('ignore_patterns', {}).get('directories', []))
                            ignored_files = set(st.session_state.config.get('ignore_patterns', {}).get('files', []))
                            
                            # Use the VS Code-style tree view
                            from frontend.components.tree_view import TreeView
                            tree_view = TreeView()
                            new_ignored_dirs, new_ignored_files = tree_view.render(
                                file_tree['contents'],
                                ignored_dirs=ignored_dirs,
                                ignored_files=ignored_files
                            )
                            
                            # Update config if patterns changed
                            if new_ignored_dirs != ignored_dirs or new_ignored_files != ignored_files:
                                st.session_state.config['ignore_patterns']['directories'] = list(new_ignored_dirs)
                                st.session_state.config['ignore_patterns']['files'] = list(new_ignored_files)
                                self.save_config(st.session_state.config)
                                if 'current_tree' in st.session_state:
                                    del st.session_state.current_tree
                                st.rerun()
                                
                        except Exception as e:
                            st.error(f"Error loading file tree: {str(e)}")
                    else:
                        st.info("Please enter a valid repository path in the Settings tab to manage file inclusion.")

            return st.session_state.config.get('local_root', '')

    def save_config(self, config_data: Dict[str, Any]):
        """Save configuration to config.yaml with proper locking."""
        try:
            with self._config_lock:
                logger.info("Saving config (excluding API keys)")
                config_path = Path('config/config.yaml')
                config_path.parent.mkdir(parents=True, exist_ok=True)

                # Create a copy of config without API keys for file saving
                save_data = config_data.copy()
                save_data['api_keys'] = {}  # Clear API keys when saving to file
                
                with open(config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(save_data, f, default_flow_style=False, sort_keys=False)

                # Keep the API keys in session state but not in file
                st.session_state.config = config_data.copy()
                return True
        except Exception as e:
            logger.error(f"Failed to save configuration: {str(e)}")
            st.error(f"Failed to save configuration: {str(e)}")
            return False

    def clear_state(self):
        """Clear all sidebar-related state."""
        # Create a fresh default config without any API keys
        fresh_config = self.default_config.copy()
        fresh_config['api_keys'] = {}
        
        st.session_state.config = fresh_config
        st.session_state.loaded_config = None
        st.session_state.loaded_rules = {}
        
        # Clear any cached data
        if 'current_tree' in st.session_state:
            del st.session_state.current_tree
        if 'crawler' in st.session_state:
            del st.session_state.crawler
        if 'config_hash' in st.session_state:
            del st.session_state.config_hash
            
        # Save the cleared config to file
        self.save_config(fresh_config)

    def load_config_file(self, uploaded_file) -> bool:
        """Load configuration from uploaded file."""
        try:
            content = uploaded_file.getvalue().decode()
            config_data = yaml.safe_load(content)
            required_keys = {'local_root', 'ignore_patterns', 'model'}
            if not all(k in config_data for k in required_keys):
                st.error("Invalid configuration file format")
                return False
            return self.save_config(config_data)
        except Exception as e:
            logger.error(f"Error loading configuration: {str(e)}")
            st.error(f"Error loading configuration: {str(e)}")
            return False
