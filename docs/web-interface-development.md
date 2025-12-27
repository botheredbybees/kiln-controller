# Introductory Course: Modifying the Kiln-Controller Web Interface

The [kiln-controller](https://github.com/botheredbybees/kiln-controller) web interface is built using a traditional client-server architecture with real-time communication capabilities. The system uses Python on the backend and jQuery-based JavaScript on the frontend with WebSocket support for live updates.

## Technology Stack Overview

The web interface relies on several key technologies that work together to provide temperature monitoring and kiln control functionality.

### Backend Framework

The server uses [Bottle](https://bottlepy.org/), a lightweight Python web framework that provides routing, static file serving, and HTTP request handling. Bottle is particularly well-suited for small applications like this because it's a single-file module with no dependencies beyond the Python standard library. The server runs on [gevent](http://www.gevent.org/), a coroutine-based networking library that enables efficient handling of concurrent WebSocket connections.

### Real-Time Communication

WebSocket connections handle bidirectional communication between the browser and Raspberry Pi using [gevent-websocket](https://pypi.org/project/gevent-websocket/). Four separate WebSocket channels manage different aspects of the system:

- `/status` - streams live temperature data and kiln state
- `/control` - sends commands like start/stop
- `/config` - retrieves configuration settings
- `/storage` - manages firing profiles

### Frontend Libraries

The interface uses several JavaScript libraries:

- [jQuery 1.10.2](https://jquery.com/) for DOM manipulation and AJAX requests
- [Bootstrap 3](https://getbootstrap.com/docs/3.4/) for responsive UI components and styling
- [Flot](https://www.flotcharts.org/) for interactive temperature/time graphs with draggable data points
- [Select2](https://select2.org/) for enhanced dropdown menus when choosing firing profiles

## Project Structure

Understanding where code lives is essential for making targeted modifications to specific features.

### Backend Files

The main application logic resides in several key files:

- `kiln-controller.py` - defines all HTTP routes (`/`, `/api`, `/picoreflow/*`) and WebSocket endpoints
- `config.py` - contains user-configurable settings like temperature scale (Celsius/Fahrenheit), listening port, and GPIO pin assignments
- `lib/` directory - houses core controller logic:
  - `oven.py` - temperature control and PID algorithms
  - `ovenWatcher.py` - state monitoring and data logging

### Frontend Structure

The `public/` directory contains all client-side code:

- `public/index.html` - main interface with status displays, graphs, and control buttons
- `public/assets/js/picoreflow.js` - implements all custom JavaScript logic for WebSocket handling, graph updates, and profile management
- `public/assets/css/` - Bootstrap CSS and custom styles that define the visual appearance

## Key Code Segments

Let's examine the critical sections you'll interact with when modifying the interface.

### WebSocket Initialization (picoreflow.js lines 14-21)

```javascript
var protocol = 'ws:';
if (window.location.protocol == 'https:') {
    protocol = 'wss:';
}
var host = "" + protocol + "//" + window.location.hostname + ":" + window.location.port;
var ws_status = new WebSocket(host+"/status");
var ws_control = new WebSocket(host+"/control");
var ws_config = new WebSocket(host+"/config");
var ws_storage = new WebSocket(host+"/storage");
```

This code establishes the WebSocket connections that enable real-time updates. If you want to add new data streams or modify connection behavior, this is where to start.

### Status Updates (picoreflow.js lines 510-600)

The `ws_status.onmessage` handler processes incoming temperature and state data from the server. It:

- Updates display elements like `#act_temp`, `#target_temp`, and `#state`
- Redraws the live graph by pushing new data points to `graph.live.data`
- Manages the progress bar showing firing schedule completion

### Backend Routes (kiln-controller.py lines 46-104)

The `@app.post('/api')` function handles API commands from the web interface. It processes commands like:

- `'run'` - start a firing schedule
- `'stop'` - abort the current run
- `'stats'` - retrieve PID controller statistics

To add new functionality, you would create additional command handlers here and corresponding frontend JavaScript to call them.

### Graph Rendering (picoreflow.js lines 320-400)

The `getOptions()` function configures the Flot charting library with axis settings, colors, and interactivity options. The `updateProfile()` function loads selected firing schedules into the graph and calculates estimated runtime and power costs.

## Common Modifications

These examples demonstrate typical customization scenarios with specific implementation guidance.

### Adding a New Display Element

To add a humidity sensor display:

1. Modify `public/index.html` to include a new status panel element with an ID like `#humidity`
2. Update the backend in `lib/oven.py` to read the sensor and include humidity data in the status dictionary that gets sent via WebSocket
3. In `picoreflow.js`, modify the `ws_status.onmessage` handler to extract the humidity value from the incoming data (`x.humidity`) and update your display element using `$('#humidity').html(x.humidity + '%')`

### Customizing the Graph Appearance

The Flot options in `getOptions()` control all visual aspects of the temperature chart:

- To change colors, modify the `color` property in the `graph.profile` and `graph.live` objects (lines 29-43 in picoreflow.js)
- To adjust axis ranges, modify the `min` and `max` properties in `xaxis` and `yaxis` within `getOptions()`
- For custom tick formatting, you can modify or create functions like `timeTickFormatter()` (lines 154-176)

### Creating New API Endpoints

To add a feature like manual heater override:

1. Define a new route in `kiln-controller.py` using the `@app.post('/api')` decorator and check for a new command type like `if bottle.request.json['cmd'] == 'heater_override'`
2. Add the corresponding logic to control the heater based on the request parameters
3. On the frontend, create a button in `index.html` and attach a click handler that sends the command via `$.ajax()` or through the control WebSocket

## Development Workflow

A practical approach to safely modifying and testing changes requires careful attention to preserving working configurations.

### Local Testing

Before deploying changes to your Raspberry Pi, you can run the controller in simulation mode:

1. Set `simulate = True` in `config.py`
2. Run the application locally with `python kiln-controller.py`
3. Access the interface at `http://localhost:8081` (or your configured port)

This allows testing the web interface without requiring actual hardware connections.

### Browser Developer Tools

Use your browser's JavaScript console (F12 in most browsers) to:

- Monitor WebSocket messages
- Debug JavaScript errors
- Inspect the data structures being passed between client and server

The Network tab shows all HTTP requests and WebSocket frames, which is invaluable for understanding the communication flow.

### Making Changes Safely

Best practices for development:

- Always work on a copy of the repository rather than modifying the original directly
- Create a git branch for your modifications using `git checkout -b my-feature-name`
- Test changes incrementally - modify one component at a time and verify it works before moving to the next change
- Keep the browser console open to catch JavaScript errors immediately

## Worked Example: Complete Feature Implementation

For a detailed, step-by-step example of implementing a real feature from start to finish, see the [Delta Time Column Example](example-delta-time-feature.md). This guide walks through:

- Identifying a user experience problem
- Planning the solution
- Implementing the code changes
- Comprehensive testing
- Creating a pull request
- Responding to code review

The example demonstrates adding a "Time Increment" column to the profile editor, making it easier to create and modify firing schedules without manual time calculations.

## Additional Resources

For deeper understanding of the underlying technologies, consult these authoritative sources:

- [Bottle documentation](https://bottlepy.org/docs/dev/) - explains routing, templating, and request handling
- [WebSocket API documentation](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket) on MDN - covers client-side WebSocket programming
- [Flot documentation](https://github.com/flot/flot/blob/master/API.md) - provides comprehensive charting options
- [jQuery API documentation](https://api.jquery.com/) - the definitive reference for jQuery
- [Bootstrap 3 documentation](https://getbootstrap.com/docs/3.4/getting-started/) - covers all UI components used in the interface

## Next Steps

After becoming familiar with the basic structure:

1. Start by making small visual changes to build confidence
2. Experiment with modifying existing features before adding completely new ones
3. Use the browser console to understand the data flow
4. Work through the [complete example](example-delta-time-feature.md) to see the full development workflow
5. Refer to the existing code in `picoreflow.js` for patterns and examples
6. Join the community discussions on the [GitHub repository](https://github.com/botheredbybees/kiln-controller) if you need help
