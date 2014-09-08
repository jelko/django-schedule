from schedule.utils import serialize_occurrences
from urllib import quote
from django.shortcuts import render_to_response, get_object_or_404
from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.template import RequestContext
from django.template import Context, loader
from django.core import serializers
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
import datetime

from schedule.conf.settings import GET_EVENTS_FUNC, OCCURRENCE_CANCEL_REDIRECT
from schedule.forms import EventForm, OccurrenceForm
from schedule.forms import EventBackendForm, OccurrenceBackendForm
from schedule.models import *
from schedule.periods import weekday_names
from schedule.utils import check_event_permissions, coerce_date_dict
from schedule.utils import decode_occurrence, serialize_occurrences

def calendar(request, calendar_slug, template='schedule/calendar.html'):
    """
    This view returns a calendar.  This view should be used if you are
    interested in the meta data of a calendar, not if you want to display a
    calendar.  It is suggested that you use calendar_by_periods if you would
    like to display a calendar.

    Context Variables:

    ``calendar``
        The Calendar object designated by the ``calendar_slug``.
    """
    calendar = get_object_or_404(Calendar, slug=calendar_slug)
    return render_to_response(template, {
        "calendar": calendar,
    }, context_instance=RequestContext(request))

def calendar_by_periods(request, calendar_slug, periods=None,
    template_name="schedule/calendar_by_period.html"):
    """
    This view is for getting a calendar, but also getting periods with that
    calendar.  Which periods you get, is designated with the list periods. You
    can designate which date you the periods to be initialized to by passing
    a date in request.GET. See the template tag ``query_string_for_date``

    Context Variables

    ``date``
        This was the date that was generated from the query string.

    ``periods``
        this is a dictionary that returns the periods from the list you passed
        in.  If you passed in Month and Day, then your dictionary would look
        like this

        {
            'month': <schedule.periods.Month object>
            'day':   <schedule.periods.Day object>
        }

        So in the template to access the Day period in the context you simply
        use ``periods.day``.

    ``calendar``
        This is the Calendar that is designated by the ``calendar_slug``.

    ``weekday_names``
        This is for convenience. It returns the local names of weekedays for
        internationalization.

    """
    calendar = get_object_or_404(Calendar, slug=calendar_slug)
    date = coerce_date_dict(request.GET)
    if date:
        try:
            date = datetime.datetime(**date)
        except ValueError:
            raise Http404
    else:
        date = datetime.datetime.now()
    event_list = GET_EVENTS_FUNC(request, calendar)
    period_objects = dict([(period.__name__.lower(), period(event_list, date)) for period in periods])
    return render_to_response(template_name,{
            'date': date,
            'periods': period_objects,
            'calendar': calendar,
            'weekday_names': weekday_names,
            'here':quote(request.get_full_path()),
        },context_instance=RequestContext(request),)

def event(request, event_id, template_name="schedule/event.html"):
    """
    This view is for showing an event. It is important to remember that an
    event is not an occurrence.  Events define a set of reccurring occurrences.
    If you would like to display an occurrence (a single instance of a
    recurring event) use occurrence.

    Context Variables:

    event
        This is the event designated by the event_id

    back_url
        this is the url that referred to this view.
    """
    event = get_object_or_404(Event, id=event_id)
    back_url = request.META.get('HTTP_REFERER', None)
    try:
        cal = event.calendar_set.get()
    except:
        cal = None
    return render_to_response(template_name, {
        "event": event,
        "back_url" : back_url,
    }, context_instance=RequestContext(request))

def occurrence(request, event_id,
    template_name="schedule/occurrence.html", *args, **kwargs):
    """
    This view is used to display an occurrence.

    Context Variables:

    ``event``
        the event that produces the occurrence

    ``occurrence``
        the occurrence to be displayed

    ``back_url``
        the url from which this request was refered
    """
    event, occurrence = get_occurrence(event_id, *args, **kwargs)
    back_url = request.META.get('HTTP_REFERER', None)
    return render_to_response(template_name, {
        'event': event,
        'occurrence': occurrence,
        'back_url': back_url,
    }, context_instance=RequestContext(request))

def get_occurrence(event_id, occurrence_id=None, year=None, month=None,
    day=None, hour=None, minute=None, second=None):
    """
    Because occurrences don't have to be persisted, there must be two ways to
    retrieve them. both need an event, but if its persisted the occurrence can
    be retrieved with an id. If it is not persisted it takes a date to
    retrieve it.  This function returns an event and occurrence regardless of
    which method is used.
    """
    if(occurrence_id):
        occurrence = get_object_or_404(Occurrence, id=occurrence_id)
        event = occurrence.event
    elif not [x for x in (year, month, day, hour, minute, second) if x is None]:
        event = get_object_or_404(Event, id=event_id)
        occurrence = event.get_occurrence(
            datetime.datetime(int(year), int(month), int(day), int(hour),
                int(minute), int(second)))
        if occurrence is None:
            raise Http404
    else:
        raise Http404
    return event, occurrence

def check_next_url(next):
    """
    Checks to make sure the next url is not redirecting to another page.
    Basically it is a minimal security check.
    """
    if not next or '://' in next:
        return None
    return next

def get_next_url(request, default):
    next = default
    if OCCURRENCE_CANCEL_REDIRECT:
        next = OCCURRENCE_CANCEL_REDIRECT
    if 'next' in request.REQUEST and check_next_url(request.REQUEST['next']) is not None:
        next = request.REQUEST['next']
    return next


class JSONError(HttpResponse):

    def __init__(self, error):
        s = "{error:'%s'}" % error
        HttpResponse.__init__(self, s)
        # TODO strip html tags from form errors


def calendar_by_periods_json(request, calendar_slug, periods):
    # XXX is this function name good?
    # it conforms with the standard API structure but in this case it is rather cryptic
    user = request.user
    calendar = get_object_or_404(Calendar, slug=calendar_slug)
    date = coerce_date_dict(request.GET)
    if date:
        try:
            date = datetime.datetime(**date)
        except ValueError:
            raise Http404
    else:
        date = datetime.datetime.now()
    event_list = GET_EVENTS_FUNC(request, calendar)
    period_object = periods[0](event_list, date)
    occurrences = []
    for o in period_object.occurrences:
        if period_object.classify_occurrence(o):
            occurrences.append(o)
    resp = serialize_occurrences(occurrences, user)
    return HttpResponse(resp)


# TODO permissions check
def ajax_edit_occurrence_by_code(request):
    try:
        id = request.REQUEST.get('id')
        kwargs = decode_occurrence(id)
        event_id = kwargs.pop('event_id')
        event, occurrence = get_occurrence(event_id, **kwargs)
        if request.REQUEST.get('action') == 'cancel':
            occurrence.cancel()
            return HttpResponse(serialize_occurrences([occurrence], request.user))
        form = OccurrenceBackendForm(data=request.POST or None, instance=occurrence)
        if form.is_valid():
            occurrence = form.save(commit=False)
            occurrence.event = event
            occurrence.save()
            return HttpResponse(serialize_occurrences([occurrence], request.user))
        return JSONError(form.errors)
    except Exception, e:
        import traceback
        traceback.print_exc()
        return JSONError(e)


#TODO permission control
def ajax_edit_event(request, calendar_slug):
    print request.POST
    try:
        id = request.REQUEST.get('id') # we got occurrence's encoded id or event id
        if id:
            kwargs = decode_occurrence(id)
            if kwargs:
                event_id = kwargs['event_id']
            else:
                event_id = id
            event = Event.objects.get(pk=event_id)
            # deleting an event
            if request.REQUEST.get('action') == 'cancel':
                # cancellation of a non-recurring event means deleting the event
                event.delete()
                # there is nothing more - we return empty json
                return HttpResponse(serialize_occurrences([], request.user))
            else:
                form = EventBackendForm(data=request.POST, instance=event)
                if form.is_valid():
                    event = form.save()
                    return HttpResponse(serialize_occurrences(event.get_occurrences(event.start, event.end), request.user))
                return JSONError(form.errors)
        else:
            calendar = get_object_or_404(Calendar, slug=calendar_slug)
            # creation of an event
            form = EventBackendForm(data=request.POST)
            if form.is_valid():
                event = form.save(commit=False)
                event.creator = request.user
                event.calendar = calendar
                event.save()
                return HttpResponse(serialize_occurrences(event.get_occurrences(event.start, event.end), request.user))
            return JSONError(form.errors)
    except Exception, e:
        import traceback
        traceback.print_exc()
        return JSONError(e)


#TODO permission control
def event_json(request):
    event_id = request.REQUEST.get('event_id')
    event = get_object_or_404(Event, pk=event_id)
    event.rule_id = event.rule_id or "false"
    rnd = loader.get_template('schedule/event_json.html')
    resp = rnd.render(Context({'event':event}))
    return HttpResponse(resp)
