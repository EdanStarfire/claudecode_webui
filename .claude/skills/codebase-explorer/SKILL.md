---
name: codebase-explorer
description: Identify relevant files, understand architecture patterns, and find code by functionality. Use when you need to understand where specific functionality lives or find files related to a feature area.
---

# Codebase Explorer

## Instructions

### When to Invoke This Skill
- Need to find where specific functionality is implemented
- Understanding codebase structure before making changes
- Locating files related to a feature or bug
- Discovering architectural patterns in the project
- Finding examples of similar functionality
- Understanding dependencies between components

### Exploration Strategies

#### 1. Top-Down Exploration (Start Broad)

**Use when:** You know the general area but not specific files.

**Approach:**
1. Start with project structure overview
2. Identify likely directories
3. Find entry points
4. Trace execution flow

#### 2. Bottom-Up Exploration (Start Specific)

**Use when:** You know specific terms, functions, or classes.

**Approach:**
1. Search for specific strings
2. Find definitions
3. Trace usages
4. Understand context

#### 3. Pattern-Based Exploration

**Use when:** Looking for similar implementations.

**Approach:**
1. Find one example
2. Search for similar patterns
3. Identify conventions
4. Apply understanding

### Tools and Techniques

#### Using Glob to Find Files

**Find by pattern:**
```bash
# All Python files
**/*.py

# All Vue components
frontend/src/components/**/*.vue

# All test files
**/test_*.py
src/tests/**/*.py

# Specific area
src/legion/**/*.py

# Configuration files
**/*.json
**/*.md
```

**Find entry points:**
```bash
# Main entry
main.py
**/main.py

# Server files
**/server.py
**/*_server.py

# Index files
**/index.html
**/index.js
```

#### Using Grep to Search Code

**Find function definitions:**
```bash
# Python functions
def <function_name>

# Python classes
class <ClassName>

# JavaScript/TypeScript functions
function <functionName>
const <functionName> =

# Vue components
export default {
```

**Find usages:**
```bash
# Where is function called?
<function_name>(

# Where is class instantiated?
<ClassName>(

# Where is import used?
from .* import .*<name>
import .* from
```

**Find by functionality:**
```bash
# WebSocket code
websocket
WebSocket

# Database operations
SELECT |INSERT |UPDATE |DELETE

# API endpoints
@app\.route|@router\.(get|post|put|delete)

# Error handling
try:|except |raise |throw
```

**Find configuration:**
```bash
# Environment variables
os\.environ|process\.env

# Configuration files
config|settings|\.env
```

### Exploration Workflows

#### Workflow 1: Finding Feature Implementation

**Goal:** Find where user authentication is implemented.

**Steps:**
1. **Search for keywords:**
   ```bash
   grep -r "auth" --include="*.py"
   grep -r "login" --include="*.py"
   ```

2. **Find files:**
   ```bash
   **/*auth*.py
   **/*login*.py
   ```

3. **Read relevant files:**
   - Look for class/function definitions
   - Identify main entry points
   - Trace dependencies

4. **Find usages:**
   ```bash
   grep "authenticate(" **/*.py
   grep "login(" **/*.py
   ```

5. **Understand flow:**
   - API endpoint → Business logic → Data layer
   - Frontend → API → Backend

#### Workflow 2: Understanding Architecture

**Goal:** Understand how sessions work.

**Steps:**
1. **Find session-related files:**
   ```bash
   **/*session*.py
   ```

2. **Read main files:**
   - Start with core classes (SessionManager, etc.)
   - Understand data structures
   - Identify key methods

3. **Find related components:**
   ```bash
   grep "SessionManager" **/*.py
   grep "session_id" **/*.py
   ```

4. **Trace data flow:**
   - How sessions are created
   - Where session state is stored
   - How sessions are accessed

5. **Map dependencies:**
   - What does session management depend on?
   - What depends on session management?

#### Workflow 3: Finding Similar Implementation

**Goal:** Want to add new API endpoint, find examples.

**Steps:**
1. **Find API endpoint definitions:**
   ```bash
   grep "@app\.route\|@router\.(get|post)" **/*.py
   ```

2. **Read example endpoint:**
   - Understand parameter handling
   - See error handling pattern
   - Note response format

3. **Find related patterns:**
   ```bash
   grep "jsonify\|JSONResponse" **/*.py
   ```

4. **Identify conventions:**
   - Request validation approach
   - Error response format
   - Success response format

#### Workflow 4: Debugging Error Location

**Goal:** Error mentions "WebSocketManager", find where it's defined.

**Steps:**
1. **Find class definition:**
   ```bash
   grep "class WebSocketManager" **/*.py
   ```

2. **Read the class:**
   - Understand responsibilities
   - Note key methods
   - See error handling

3. **Find usages:**
   ```bash
   grep "WebSocketManager(" **/*.py
   ```

4. **Trace error path:**
   - Where is error raised?
   - What triggers it?
   - How to fix?

### Understanding Project Structure

#### Typical Python Backend Structure
```
src/
├── server.py             # HTTP server, API endpoints
├── coordinator.py        # Orchestration, coordination
├── models.py             # Data models, entities
├── services/             # Business logic services
├── storage.py            # Persistence layer
└── tests/                # Unit tests
    ├── test_*.py
    └── conftest.py
```

#### Typical Frontend Structure
```
frontend/src/
├── components/            # UI components
│   ├── layout/           # Layout components
│   ├── views/            # Page-level views
│   └── common/           # Reusable components
├── stores/               # State management
├── router/               # Route definitions
├── composables/          # Reusable composition functions
└── utils/                # Helper functions
```

### Reading Code Effectively

#### 1. Start with Entry Points

**Backend:**
- `main.py` - Application entry
- `web_server.py` - API endpoints
- Route handlers - Request processing

**Frontend:**
- `index.html` - HTML entry
- `main.js` - JavaScript entry
- `App.vue` - Root component
- `router/index.js` - Route definitions

#### 2. Follow the Flow

**Request Flow (Backend):**
```
API Endpoint
   ↓
Request Validation
   ↓
Business Logic (Manager/Coordinator)
   ↓
Data Layer (Storage)
   ↓
Response Formation
```

**Component Flow (Frontend):**
```
User Action
   ↓
Component Event Handler
   ↓
Store Action
   ↓
API Call (or Local State Update)
   ↓
Store State Update
   ↓
Reactive UI Update
```

#### 3. Identify Patterns

**Common Patterns to Recognize:**
- Factory pattern (create objects)
- Observer pattern (event handling)
- Repository pattern (data access)
- Coordinator pattern (orchestration)
- Component composition (UI building)

#### 4. Understand Dependencies

**Import Statements Tell Story:**
```python
from .user_service import UserService
from .project_service import ProjectService
from .storage import Database
```
This file coordinates multiple services.

**Circular Dependencies:**
If A imports B and B imports A, there's likely architectural issue.

### Common Codebase Questions

**"Where is X implemented?"**
→ Search for class/function definition: `grep "def X\|class X"`

**"How is X used?"**
→ Search for usages: `grep "X("`

**"What files handle Y functionality?"**
→ Search for keywords: `grep -r "keyword" --include="*.py"`

**"What are the API endpoints?"**
→ Search for route decorators: `grep "@app\.route\|@router\."`

**"Where is data stored?"**
→ Find storage classes, look for file I/O operations

**"How do frontend and backend communicate?"**
→ Find API calls in frontend, match to endpoints in backend

## Examples

### Example 1: Find user creation logic
```
1. Search for "create_user"
   grep "def create_user" **/*.py

2. Found in: src/coordinator.py and src/services/user_service.py

3. Read the create_user method:
   - Validates input
   - Calls service layer
   - Persists to storage

4. Understand flow:
   API endpoint → Coordinator → Service → Storage
```

### Example 2: Find frontend component for item list
```
1. Find related components:
   frontend/src/components/**/*List*.vue

2. Look for list-related:
   ItemList.vue, ItemCard.vue

3. Read ItemList.vue:
   - Uses state store
   - Renders ItemCard for each item
   - Handles selection and filtering

4. Check store:
   frontend/src/stores/items.js
   - Items stored in Map
   - CRUD operations defined
```

### Example 3: Understand WebSocket message handling
```
1. Find WebSocket code:
   grep "websocket\|WebSocket" **/*.py

2. Found: server.py has WebSocket handling

3. Read WebSocket handler:
   - Manages connections
   - Routes messages
   - Broadcasts updates

4. Find message handlers:
   grep "handle.*message" src/server.py

5. Trace message flow:
   WebSocket → Handler → Store/Coordinator → Response
```

### Example 4: Find how authentication works
```
1. Search for auth-related code:
   grep -r "auth" **/*.py

2. Found in:
   - src/middleware/auth.py (middleware)
   - src/services/auth_service.py (logic)
   - src/models.py (user model)

3. Read auth middleware:
   - Extracts token from request
   - Validates against service
   - Sets user context

4. Understand flow:
   Request → Middleware → Token validation →
   User lookup → Context set → Handler executes
```
