
from collections import defaultdict

from django.core.urlresolvers import reverse
from django.template.defaultfilters import linebreaksbr, urlize
from django.conf import settings as django_settings

from mezzanine import template
from mezzanine.conf import settings
from mezzanine.generic.forms import ThreadedCommentForm
from mezzanine.generic.forms import ReviewForm
from mezzanine.generic.models import ThreadedComment, Review, RequiredReviewRating
from mezzanine.blog.models import BlogPost
from mezzanine.utils.views import paginate

from django.contrib.contenttypes.models import ContentType
from voting.models import Vote
from django.contrib.comments.models import Comment

from django.template import TemplateSyntaxError, Node, Variable
from django.contrib.contenttypes.models import ContentType

from social_auth.models import UserSocialAuth
from social_friends_finder.models import SocialFriendList
from django.http import HttpResponse

register = template.Library()


class AsNode(Node):
    """
    Base template Node class for template tags that takes a predefined number
    of arguments, ending in an optional 'as var' section.
    """
    args_count = 3

    @classmethod
    def handle_token(cls, parser, token):
        """
        Class method to parse and return a Node.
        """
        bits = token.split_contents()
        args_count = len(bits) - 1
        if args_count >= 2 and bits[-2] == 'as':
            as_var = bits[-1]
            args_count -= 2
        else:
            as_var = None
        if args_count != cls.args_count:
            arg_list = ' '.join(['[arg]' * cls.args_count])
            raise TemplateSyntaxError("Accepted formats {%% %(tagname)s "
                "%(args)s %%} or {%% %(tagname)s %(args)s as [var] %%}" %
                {'tagname': bits[0], 'args': arg_list})
        args = [parser.compile_filter(token) for token in
            bits[1:args_count + 1]]
        return cls(args, varname=as_var)

    def __init__(self, args, varname=None):
        self.args = args
        self.varname = varname

    def render(self, context):
        result = self.render_result(context)
        if self.varname is not None:
            context[self.varname] = result
            return ''
        return result

    def render_result(self, context):
        raise NotImplementedError("Must be implemented by a subclass")

@register.inclusion_tag("generic/includes/comments.html", takes_context=True)
def comments_for(context, obj, css_class=None):
    """
    Provides a generic context variable name for the object that
    comments are being rendered for.
    """
    form = ReviewForm(context["request"], obj)
    if css_class:
        form.fields['comment'].widget.attrs['class'] = css_class
    form.fields['title'].widget.attrs['placeholder'] = 'Write a title...'
    try:
        context["posted_comment_form"]
    except KeyError:
        context["posted_comment_form"] = form

    context["reviewFilter"] = context['request'].GET.get('reviewFilter', '')
    context["unposted_comment_form"] = form
    context["comment_url"] = reverse("comment")
    context["object_for_comments"] = obj
    return context

@register.inclusion_tag("generic/includes/comments_on_objects.html", takes_context=True)
def comments_for_review(context, obj, css_class=None):
    """
    Provides a generic context variable name for the object that
    comments are being rendered for.
    """
    form = ThreadedCommentForm(context["request"], obj)
    if css_class:
        form.fields['comment'].widget.attrs['class'] = css_class
    form.fields['comment'].widget.attrs['placeholder'] = 'Write a comment...'
    form.fields['comment'].widget.attrs['rows'] = 1
    form.fields['comment'].label = ""
    context["posted_comment_form"] = form
    context["unposted_comment_form"] = form
    context["comment_url"] = reverse("comment_on_review")
    context["object_for_comments"] = obj
    return context

@register.inclusion_tag("generic/includes/comment.html", takes_context=True)
def comment_thread(context, parent):
    """
    Return a list of child comments for the given parent, storing all
    comments in a dict in the context when first called, using parents
    as keys for retrieval on subsequent recursive calls from the
    comments template.
    """
    if "all_comments" not in context:
        comments = defaultdict(list)
        if "request" in context and context["request"].user.is_staff:
            comments_queryset = parent.comments.all()
        else:
            comments_queryset = parent.comments.visible()
        for comment in comments_queryset.select_related("user"):
            comments[comment.replied_to_id].append(comment)
        context["all_comments"] = comments
    parent_id = parent.id if isinstance(parent, Review) else None
    try:
        replied_to = int(context["request"].POST["replied_to"])
    except KeyError:
        replied_to = 0

    page = context['request'].GET.get("page", 1)
    per_page = django_settings.REVIEWS_PER_PAGE
    max_paging_links = django_settings.MAX_PAGING_LINKS

    comments_queryset = context["all_comments"].get(parent_id, [])
    paginated = paginate(comments_queryset, page, per_page, max_paging_links)

    context.update({
        "comments_for_thread": paginated,
        "no_comments": parent_id is None and not context["all_comments"],
        "replied_to": replied_to,
    })
    return context

@register.inclusion_tag("generic/includes/comment.html", takes_context=True)
def comment_thread_most_recent(context, parent):
    """
    Return a list of child comments for the given parent, storing all
    comments in a dict in the context when first called, using parents
    as keys for retrieval on subsequent recursive calls from the
    comments template.
    """

    comments = None
    if "request" in context and context["request"].user.is_staff:
        comments_queryset = parent.comments.all()
    else:
        comments_queryset = parent.comments.visible()


    comments = comments_queryset.select_related("user").order_by('-submit_date')

    parent_id = parent.id if isinstance(parent, Review) else None
    
    try:
        replied_to = int(context["request"].POST["replied_to"])
    except KeyError:
        replied_to = 0

    page = context['request'].GET.get("page", 1)
    per_page = django_settings.REVIEWS_PER_PAGE
    max_paging_links = django_settings.MAX_PAGING_LINKS

    paginated = paginate(comments, page, per_page, max_paging_links)

    context.update({
        "comments_for_thread": paginated,
        "no_comments": parent_id is None and not comments,
        "replied_to": replied_to,
    })
    return context


@register.inclusion_tag("generic/includes/comment.html", takes_context=True)
def comment_thread_most_liked(context, parent):
    """
    Return a list of child comments for the given parent, storing all
    comments in a dict in the context when first called, using parents
    as keys for retrieval on subsequent recursive calls from the
    comments template.
    """
    comments = None
    if "request" in context and context["request"].user.is_staff:
        comments_queryset = parent.comments.all()
    else:
        comments_queryset = parent.comments.visible()

    model_type = ContentType.objects.get_for_model(Review)
    table_name = Comment._meta.db_table

    commentsList = comments_queryset.extra(select={
        'score': 'SELECT COALESCE(SUM(vote),0) FROM %s WHERE content_type_id=%d AND object_id=%s.id' % (Vote._meta.db_table, int(model_type.id), table_name)
    }).order_by('-score', '-submit_date', )

    comments = commentsList.select_related("user")
 
    parent_id = parent.id if isinstance(parent, Review) else None
    try:
        replied_to = int(context["request"].POST["replied_to"])
    except KeyError:
        replied_to = 0

    page = context['request'].GET.get("page", 1)
    per_page = django_settings.REVIEWS_PER_PAGE
    max_paging_links = django_settings.MAX_PAGING_LINKS

    paginated = paginate(comments, page, per_page, max_paging_links)

    context.update({
        "comments_for_thread": paginated,
        "no_comments": parent_id is None and not comments,
        "replied_to": replied_to,
    })
    return context

@register.inclusion_tag("generic/includes/comment.html", takes_context=True)
def comment_thread_social(context, parent):
    """
    Return a list of child comments for the given parent, storing all
    comments in a dict in the context when first called, using parents
    as keys for retrieval on subsequent recursive calls from the
    comments template.
    """
    comments = None

    if "request" in context and context["request"].user.is_staff:
        comments_queryset = parent.comments.all()
    else:
        comments_queryset = parent.comments.visible()

    comments_queryset = comments_queryset.order_by('-submit_date')

    #user_social_auth_list = context["request"].user.social_auth.filter(provider="facebook")
    #if not user_social_auth_list:
    #    user_social_auth_list = context["request"].user.social_auth.filter(provider="twitter")
    #if user_social_auth_list:
    #    user_social_auth = user_social_auth_list[0]    
    #    friends = SocialFriendList.objects.existing_social_friends(context["request"].user.social_auth.filter(provider="facebook")[0])
    #    for comment in comments_queryset.select_related("user"):
    #        if comment.user in friends:
    #            comments[comment.replied_to_id].append(comment)
    
    friends = context["request"].user.relationships.following()
    comments = comments_queryset.select_related("user").filter(user__in=friends)

    parent_id = parent.id if isinstance(parent, Review) else None
    try:
        replied_to = int(context["request"].POST["replied_to"])
    except KeyError:
        replied_to = 0

    page = context['request'].GET.get("page", 1)
    per_page = django_settings.REVIEWS_PER_PAGE
    max_paging_links = django_settings.MAX_PAGING_LINKS

    paginated = paginate(comments, page, per_page, max_paging_links)

    context.update({
        "comments_for_thread": paginated,
        "no_comments": parent_id is None and not comments,
        "replied_to": replied_to,
    })
    return context

@register.inclusion_tag("generic/includes/comment.html", takes_context=True)
def comment_thread_social_level2(context, parent):
    """
    Return a list of child comments for the given parent, storing all
    comments in a dict in the context when first called, using parents
    as keys for retrieval on subsequent recursive calls from the
    comments template.
    """

    comments = None
    if "request" in context and context["request"].user.is_staff:
        comments_queryset = parent.comments.all()
    else:
        comments_queryset = parent.comments.visible()

    #user_social_auth_list = context["request"].user.social_auth.filter(provider="facebook")
    #if not user_social_auth_list:
    #    user_social_auth_list = context["request"].user.social_auth.filter(provider="twitter")
    #if user_social_auth_list:
    #    user_social_auth = user_social_auth_list[0]
    #    if user_social_auth:
    #        friends_of_friends = cache.get(user_social_auth.user.username+"SocialFriendListLevel2")
    #        if  not friends_of_friends:            
    #            friends = SocialFriendList.objects.existing_social_friends(context["request"].user.social_auth.filter(provider="facebook")[0])
    #            friends_of_friends = list(friends)
    #            for friend in friends:
    #                friends_level2 = SocialFriendList.objects.existing_social_friends(friend.social_auth.filter(provider="facebook")[0])
    #                friends_of_friends = list(chain(friends_of_friends, friends_level2))
    #            cache.set(user_social_auth.user.username+"SocialFriendListLevel2", friends_of_friends)
    friends_of_friends = set()

    friends = context["request"].user.relationships.following()
    for friend in friends:
        friends_of_friends.add(friend)
        friends_level2 = friend.relationships.following()
        for friend_level2 in friends_level2:
            friends_of_friends.add(friend_level2)

    comments_queryset = comments_queryset.order_by('-submit_date')

    comments = comments_queryset.select_related("user").filter(user__in=friends_of_friends)

    parent_id = parent.id if isinstance(parent, Review) else None
    try:
        replied_to = int(context["request"].POST["replied_to"])
    except KeyError:
        replied_to = 0

    page = context['request'].GET.get("page", 1)
    per_page = django_settings.REVIEWS_PER_PAGE
    max_paging_links = django_settings.MAX_PAGING_LINKS

    paginated = paginate(comments, page, per_page, max_paging_links)

    context.update({
        "comments_for_thread": paginated,
        "no_comments": parent_id is None and not comments,
        "replied_to": replied_to,
    })
    return context


@register.inclusion_tag("admin/includes/recent_comments.html",
    takes_context=True)
def recent_comments(context):
    """
    Dashboard widget for displaying recent comments.
    """
    latest = context["settings"].COMMENTS_NUM_LATEST
    comments = ThreadedComment.objects.all().select_related("user")
    context["comments"] = comments.order_by("-id")[:latest]
    return context

@register.inclusion_tag("admin/includes/recent_comments.html",
    takes_context=True)
def recent_reviews(context):
    """
    Dashboard widget for displaying recent comments.
    """
    comments = []
    latest = context["settings"].COMMENTS_NUM_LATEST
    comments_queryset = ThreadedComment.objects.all()
    for comment in comments_queryset.select_related("user"):
        if isinstance(comment.content_object, BlogPost):
            comments.append(comment) 
    context["comments"] = comments
    return context

@register.assignment_tag(name='comment_count_on_object')
def get_number_of_comments_on_obj(object):
    comments_queryset = None
    if object:
        comments_queryset = object.comments.visible().select_related("user")
    return len(comments_queryset)    

@register.filter
def comment_filter(comment_text):
    """
    Passed comment text to be rendered through the function defined
    by the ``COMMENT_FILTER`` setting. If no function is defined
    (the default), Django's ``linebreaksbr`` and ``urlize`` filters
    are used.
    """
    filter_func = settings.COMMENT_FILTER
    if not filter_func:
        filter_func = lambda s: linebreaksbr(urlize(s))
    return filter_func(comment_text)

@register.filter
def get_class_name(value):
    return value.__class__.__name__

@register.tag
def write_review_for_obj_url(parser, token):
    bits = token.split_contents()
    if len(bits) != 2:
        raise TemplateSyntaxError("Accepted format {% write_review_for_obj_url [instance] %}")
    else:
        return ReviewForObjURL(bits[1])

class ReviewForObjURL(Node):
    def __init__(self, obj):
        self.obj = Variable(obj)

    def render(self, context):
        obj_instance = self.obj.resolve(context)
        content_type = ContentType.objects.get_for_model(obj_instance).pk
        return reverse('write_review', kwargs={'content_type_id': content_type, 'object_id': obj_instance.pk })


@register.tag
def comments_for_obj_url(parser, token):
    bits = token.split_contents()
    if len(bits) != 2:
        raise TemplateSyntaxError("Accepted format {% comments_for_obj_url [instance] %}")
    else:
        return CommentsForObjURL(bits[1])

class CommentsForObjURL(Node):
    def __init__(self, obj):
        self.obj = Variable(obj)

    def render(self, context):
        obj_instance = self.obj.resolve(context)
        content_type = ContentType.objects.get_for_model(obj_instance).pk
        return reverse('fetch_comments_on_obj', kwargs={'content_type_id': content_type, 'object_id': obj_instance.pk })

class CommentsForObjRangeURL(AsNode):
    def render_result(self, context):
        object_instance = self.args[0].resolve(context)
        sIndex = self.args[1].resolve(context)
        lIndex = self.args[2].resolve(context)
        content_type = ContentType.objects.get_for_model(object_instance).pk
        
        return reverse('fetch_range_comments_on_obj', kwargs={
            'content_type_id': content_type, 'object_id': object_instance.pk, 'sIndex':sIndex, 'lIndex':lIndex})

@register.tag
def comments_subset_url(parser, token):
    bits = token.split_contents()
    if len(bits) != 6:
        raise TemplateSyntaxError("Accepted format "
                                  "{% comments_subset_url [object_instance] sIndex lIndex as comments_range_url %}")
    else:
        return CommentsForObjRangeURL.handle_token(parser, token)

@register.tag
def get_commenters_url(parser, token):
    bits = token.split_contents()
    if len(bits) != 2:
        raise TemplateSyntaxError("Accepted format {% comments_for_obj_url [instance] %}")
    else:
        return GetCommentersForObjURL(bits[1])

class GetCommentersForObjURL(Node):
    def __init__(self, obj):
        self.obj = Variable(obj)

    def render(self, context):
        obj_instance = self.obj.resolve(context)
        content_type = ContentType.objects.get_for_model(obj_instance).pk
        return reverse('fetch_commenters_on_obj', kwargs={'content_type_id': content_type, 'object_id': obj_instance.pk })

class CommentersRangeForObjURL(AsNode):
    def render_result(self, context):
        object_instance = self.args[0].resolve(context)
        sIndex = self.args[1].resolve(context)
        lIndex = self.args[2].resolve(context)
        content_type = ContentType.objects.get_for_model(object_instance).pk
        
        return reverse('fetch_range_commenters_on_obj', kwargs={
            'content_type_id': content_type, 'object_id': object_instance.pk, 'sIndex':sIndex, 'lIndex':lIndex})

@register.tag
def get_range_commenters_url(parser, token):
    bits = token.split_contents()
    if len(bits) != 6:
        raise TemplateSyntaxError("Accepted format "
                                  "{% get_range_commenters_url [object_instance] sIndex lIndex as commenters_range_url %}")
    else:
        return CommentersRangeForObjURL.handle_token(parser, token)

@register.inclusion_tag("generic/includes/render_comment.html", takes_context=True)
def render_comment(context, comment):
    context.update({
        "comment": comment,
    })
    return context

@register.filter
def get_reviewrating_obj(review):
    try:
        review_rating_obj = RequiredReviewRating.objects.get(commentid=review.id)
        return review_rating_obj
    except:
        return None

class GetCommentsSubsetForObj(AsNode):
    def render_result(self, context):
        object_instance = self.args[0].resolve(context)
        sIndex = self.args[1].resolve(context)
        lIndex = self.args[2].resolve(context)
        request = context.get('request')
        if request is None:
            return ''
        comments_queryset = None

        if request.user.is_staff:
            comments_queryset = object_instance.comments.all()
        else:
            comments_queryset = object_instance.comments.visible()        
        
        comments_queryset = comments_queryset.select_related("user").order_by('-submit_date')[sIndex:lIndex]
        return list(comments_queryset)[::-1]

@register.tag
def get_comments_subset(parser, token):
    bits = token.split_contents()
    if len(bits) != 6:
        raise TemplateSyntaxError("Accepted format "
                                  "{% get_comments_subset [object_instance] sIndex lIndex as comments %}")
    else:
        return GetCommentsSubsetForObj.handle_token(parser, token)

